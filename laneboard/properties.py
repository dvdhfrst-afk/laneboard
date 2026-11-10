"""Card properties: typed values a board asks every card for, like points, a priority or a link.

Each property type knows how to check and clean its own definition, check and clean a stored value,
read the text form people type on the command line or put in an imported file, write a value as
text, and sort values. The store, search, stats, formats and the command line all go through `TYPES`,
so no other module needs to know which kinds of property exist.

Stored values are plain JSON: a string, a finite number, a list of choice ids, or true. A card keeps
only the values that are set (`card["properties"]`, keyed by property id); a missing key means empty.
"""
from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal

from . import schema
from .schema import (COLORS, DEFAULT_RATING_MAX, ID_RE, MAX_CHOICE_NAME, MAX_CHOICES, MAX_DECIMALS, MAX_MULTI,
                     MAX_NUMBER, MAX_PROPERTIES, MAX_PROPERTY_NAME, MAX_PROPERTY_TEXT, MAX_UNIT, RATING_MAX_RANGE,
                     StoreError, _is_int, _is_str, _require, slug)

TYPE_NAMES = ("text", "number", "choice", "multi", "checkbox", "url", "rating")

# Ids a property never gets: card face entries, sort names and search words already mean something else.
# A property can still be named "Summary"; its id becomes "summary-2".
RESERVED_IDS = frozenset({
    "blocked", "checklist", "col", "column", "created", "field", "group", "has", "is", "key", "label", "labels",
    "notes", "progress", "rank", "sort", "summary", "tasks", "title", "updated"})

_ORDERED = frozenset({"=", "<", "<=", ">", ">=", ".."})
_NUMBER_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_RATING_TEXT = re.compile(r"([0-9]+)\s*(?:/\s*([0-9]+))?")
_URL = re.compile(r"https?://[^\s\x00-\x1f\x7f-\x9f\ud800-\udfff]+", re.IGNORECASE)
_NAME_GAPS = re.compile(r"[\s-]+")
_YES = frozenset({"yes", "true", "1", "y"})
_NO = frozenset({"no", "false", "0", "n"})


def _name_key(name):
    """How property names are told apart and looked up: ignoring case, spaces and hyphens."""
    return _NAME_GAPS.sub("", name.casefold())


def _shown(given):
    """Something a person typed, short enough to repeat in a message."""
    return given if len(given) <= 60 else given[:57] + "..."


def _given(raw, key, old, default):
    """A field from new input; when it's left out, the old definition's value (or the default)."""
    if key in raw:
        return raw[key]
    return old[key] if old is not None and key in old else default


def _is_number(value):
    return not isinstance(value, bool) and (isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)))


def _round(value, decimals):
    """Round half away from zero, as people do (2.5 to 3, 2.675 to 2.68). Whole results come back as int."""
    exact = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    done = exact.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return int(done) if done == done.to_integral_value() else float(done)


def _plain(number):
    """A number as people write it: 3, 2.5, 0.0001, 1000000 (no exponent, no trailing zeros)."""
    shown = format(Decimal(repr(number)).normalize(), "f") if isinstance(number, float) else str(number)
    return "0" if shown == "-0" else shown


def _assign_ids(entries, old_items, keep_any, avoid, key):
    """Pick an id for each entry of a whole-list update: the properties of a board, or one property's choices.

    `entries` is a list of (raw dict, cleaned name). An entry keeps the id it sends when that id is in
    `old_items` (a rename keeps its id), or when `keep_any` is set and the id is well formed (an import).
    An entry without a usable id takes the id of the old item with the same name, if nothing else kept it.
    Anything else gets a slug of its name that no old item has, so a new entry never inherits the values
    of one removed in the same change, and that isn't in `avoid`.
    """
    old_ids = {item["id"] for item in old_items}
    out, claimed = [None] * len(entries), set()
    for i, (raw, _) in enumerate(entries):
        given = raw.get("id")
        if (isinstance(given, str) and given not in claimed and given not in avoid
                and (given in old_ids or (keep_any and ID_RE.fullmatch(given)))):
            out[i] = given
            claimed.add(given)
    by_name = {}
    for item in old_items:
        by_name.setdefault(key(item["name"]), item["id"])
    for i, (_, name) in enumerate(entries):
        match = by_name.get(key(name))
        if out[i] is None and match is not None and match not in claimed:
            out[i] = match
            claimed.add(match)
    taken = claimed | old_ids | set(avoid)
    for i, (_, name) in enumerate(entries):
        if out[i] is None:
            out[i] = slug(name, taken)
            taken.add(out[i])
    return out


class PropertyType:
    """One kind of property. There is one instance of each subclass, in `TYPES`.

    `comparisons` lists the search operators that make sense for the type. An empty set means search
    matches words inside `format_text` instead (text and links). Subclasses provide `_clean` (a value
    from API JSON, not empty), `_parse` (cleaned text, not empty) and `_problem` (what is wrong with a
    stored value, or None), and may override `_clean_options`, `_check_options` and `_refit`.
    """
    name = ""
    comparisons = frozenset()
    _text_limit = MAX_PROPERTY_TEXT  # longest text form `parse_text` reads
    _family = ""  # types in one family store values the same way, so a property can swap between them

    # ---- definitions

    def check_definition(self, prop, where):
        """File check for one stored definition. Fills in options that are missing; raises BadFile."""
        _require(isinstance(prop, dict) and _is_str(prop.get("id")) and _is_str(prop.get("name")),
                 where, "each property needs an id and a name.")
        at = f"{where}, property {prop['name']!r}"
        _require(ID_RE.fullmatch(prop["id"]), at, "its id must be lowercase letters, digits and hyphens.")
        _require(prop["id"] not in RESERVED_IDS, at, f"its id {prop['id']!r} already means something else on a board.")
        _require(prop.get("type") == self.name, at, f"its type must be {self.name}.")
        prop.setdefault("onCard", False)
        _require(isinstance(prop["onCard"], bool), at, "onCard must be true or false.")
        self._check_options(prop, at)

    def clean_definition(self, raw, old=None, values=()):
        """A definition from the API or the command line, cleaned into what gets stored. Raises StoreError.

        `old` is the stored definition this replaces, if any: its id is kept, and fields left out of
        `raw` keep their old values (send null to clear `min` or `max`). A new definition's id is a slug
        of its name; `clean_definitions` makes it unique on the board.

        Changing the type of an existing property never loses data. `values` are the values cards hold
        for `old` (`clean_definitions` passes them). With no values, any type change is allowed. With
        values, only text and link, number and rating, or choice and multi can swap, and only when every
        value already fits the new type or converts without losing anything (a choice becomes a multi of
        one, a multi of one choice becomes a choice). Anything else is refused, saying how many cards are
        in the way, so the person can clear those values or add a new property instead.
        """
        if not isinstance(raw, dict):
            raise StoreError("Each property needs a name and a type.")
        name = schema.text(_given(raw, "name", old, None), "Property name", MAX_PROPERTY_NAME, required=True)
        on_card = _given(raw, "onCard", old, False)
        if not isinstance(on_card, bool):
            raise StoreError(f"Whether {name} shows on cards must be true or false.")
        prop = {"id": old["id"] if old is not None else slug(name, RESERVED_IDS), "name": name, "type": self.name,
                "onCard": on_card}
        prop.update(self._clean_options(raw, name, old))
        if old is not None and old.get("type") != self.name:
            if getattr(TYPES.get(old.get("type")), "_family", None) != self._family:
                stuck = sum(1 for _ in values)
            else:
                stuck = sum(1 for value in values if self._problem(value, prop) is not None and self._refit(value, prop)[1])
            if stuck:
                cards = "1 card has a value" if stuck == 1 else f"{stuck} cards have values"
                raise StoreError(f"{name} can't change from {old.get('type')} to {self.name} because {cards} that "
                                 f"wouldn't fit. Clear {'it' if stuck == 1 else 'them'} first, or add a new property instead.")
        return prop

    def _same_kind(self, old):
        """Whether an old definition's options carry over to this type."""
        return old is not None and old.get("type") == self.name

    def _option(self, raw, key, old, default):
        return _given(raw, key, old if self._same_kind(old) else None, default)

    def _clean_options(self, raw, name, old):
        """This type's own options, cleaned. Types without options store none."""
        return {}

    def _check_options(self, prop, where):
        """File check for this type's own options. Types without options have nothing to check."""

    # ---- values

    def check_value(self, value, prop, where):
        """File check for one stored value. `where` names the card. Raises BadFile."""
        problem = self._problem(value, prop)
        _require(problem is None, where, f"the value of {prop['name']!r} {problem}.")

    def clean_value(self, value, prop):
        """A value from API JSON, cleaned for storing. None, "" or only spaces clear it (None). Raises StoreError."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return self._clean(value, prop)

    def parse_text(self, text, prop):
        """A value from the command line or an imported file. "" clears it (None). Raises StoreError."""
        cleaned = schema.text(text, prop["name"], self._text_limit)
        return self._parse(cleaned, prop) if cleaned else None

    def format_text(self, value, prop):
        """A stored value as plain text, for search words, exports and the command line. `parse_text` reads it back."""
        return value

    def sort_key(self, value, prop):
        """A key that orders stored values of this property. Never called with None: empty values sort last."""
        return value

    def _refit(self, value, prop):
        """A value that doesn't fit `prop` any more, made to fit if possible. Returns (value or None, lost something)."""
        return (value, False) if self._problem(value, prop) is None else (None, True)


class TextType(PropertyType):
    name = "text"
    _family = "words"

    def _clean(self, value, prop):
        return schema.text(value, prop["name"], MAX_PROPERTY_TEXT) or None

    _parse = _clean

    def sort_key(self, value, prop):
        return value.casefold()

    def _problem(self, value, prop):
        if not isinstance(value, str) or not value:
            return "must be text"
        if len(value) > MAX_PROPERTY_TEXT:
            return f"is longer than {MAX_PROPERTY_TEXT} characters"
        if schema.text(value, prop["name"], MAX_PROPERTY_TEXT) != value:
            return "must be one line, with no control characters or spaces at the ends"
        return None


class NumberType(PropertyType):
    name = "number"
    comparisons = _ORDERED
    _family = "number"

    def _clean_options(self, raw, name, old):
        low = self._bound(self._option(raw, "min", old, None), f"The smallest value for {name}")
        high = self._bound(self._option(raw, "max", old, None), f"The largest value for {name}")
        if low is not None and high is not None and low > high:
            raise StoreError(f"The smallest value for {name} can't be more than the largest.")
        decimals = self._option(raw, "decimals", old, 0)
        if decimals in (None, ""):
            decimals = 0
        if not _is_int(decimals) or not 0 <= decimals <= MAX_DECIMALS:
            raise StoreError(f"Decimal places for {name} must be a whole number from 0 to {MAX_DECIMALS}.")
        unit = schema.text(self._option(raw, "unit", old, ""), f"The unit for {name}", MAX_UNIT)
        return {"min": low, "max": high, "decimals": decimals, "unit": unit}

    @staticmethod
    def _bound(value, label):
        if value is None or value == "":
            return None
        if not _is_number(value):
            raise StoreError(f"{label} must be a number, or empty.")
        if abs(value) > MAX_NUMBER:
            raise StoreError(f"{label} must be from -{MAX_NUMBER} to {MAX_NUMBER}.")
        return int(value) if isinstance(value, float) and value.is_integer() else value

    def _check_options(self, prop, where):
        for key in ("min", "max"):
            prop.setdefault(key, None)
            _require(prop[key] is None or _is_number(prop[key]), where, f"{key} must be a number or null.")
        _require(prop["min"] is None or prop["max"] is None or prop["min"] <= prop["max"], where, "min can't be more than max.")
        prop.setdefault("decimals", 0)
        _require(_is_int(prop["decimals"]) and 0 <= prop["decimals"] <= MAX_DECIMALS,
                 where, f"decimals must be a whole number from 0 to {MAX_DECIMALS}.")
        prop.setdefault("unit", "")
        _require(_is_str(prop["unit"]), where, "unit must be text.")

    @staticmethod
    def _range_words(low, high):
        if low is not None and high is not None:
            return f" from {_plain(low)} to {_plain(high)}"
        if low is not None:
            return f" of {_plain(low)} or more"
        if high is not None:
            return f" of {_plain(high)} or less"
        return ""

    def _wants(self, prop, huge=False):
        low, high = prop["min"], prop["max"]
        if huge:
            low = -MAX_NUMBER if low is None else low
            high = MAX_NUMBER if high is None else high
        return f"{prop['name']} must be a number{self._range_words(low, high)}."

    @staticmethod
    def _in_range(value, prop):
        return (prop["min"] is None or value >= prop["min"]) and (prop["max"] is None or value <= prop["max"])

    def _clean(self, value, prop):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and math.isnan(value)):
            raise StoreError(self._wants(prop))
        if abs(value) > MAX_NUMBER:  # infinity too
            raise StoreError(self._wants(prop, huge=True))
        rounded = _round(value, prop["decimals"])
        if not self._in_range(rounded, prop):
            raise StoreError(self._wants(prop))
        return rounded

    def _parse(self, text, prop):
        unit = prop["unit"]
        if unit and len(text) > len(unit) and text[-len(unit):].casefold() == unit.casefold():
            text = text[:-len(unit)].rstrip()
        if not _NUMBER_TEXT.fullmatch(text):
            if "," in text:
                raise StoreError(f"{prop['name']} must be a number written without commas, like 1200 or 2.5.")
            raise StoreError(self._wants(prop))
        number = float(text) if "." in text else int(text)
        return self._clean(number, prop)

    def format_text(self, value, prop):
        return f"{_plain(value)} {prop['unit']}" if prop["unit"] else _plain(value)

    def _problem(self, value, prop):
        if not _is_number(value) or abs(value) > MAX_NUMBER:
            return "must be a number"
        if _round(value, prop["decimals"]) != value:
            return f"has more than {prop['decimals']} decimal place{'' if prop['decimals'] == 1 else 's'}"
        if not self._in_range(value, prop):
            return f"must be a number{self._range_words(prop['min'], prop['max'])}"
        return None

    def _refit(self, value, prop):
        """Rounds to fewer decimal places; drops a value outside a new smallest or largest value."""
        try:
            fitted = self._clean(value, prop)
        except StoreError:
            return None, True
        return fitted, fitted != value


class ChoiceType(PropertyType):
    name = "choice"
    comparisons = frozenset({"="})
    _family = "choices"

    def _same_kind(self, old):
        return old is not None and old.get("type") in ("choice", "multi")

    def _clean_options(self, raw, name, old):
        given = self._option(raw, "choices", old, None)
        if given is None:
            raise StoreError(f"{name} needs at least one choice.")
        if not isinstance(given, list):
            raise StoreError(f"The choices for {name} must be a list.")
        if not given:
            raise StoreError(f"{name} needs at least one choice.")
        if len(given) > MAX_CHOICES:
            raise StoreError(f"{name} can have at most {MAX_CHOICES} choices.")
        entries, seen = [], set()
        for entry in given:
            entry = {"name": entry} if isinstance(entry, str) else entry
            if not isinstance(entry, dict):
                raise StoreError(f"Each choice for {name} needs a name.")
            label = schema.text(entry.get("name"), f"A choice name in {name}", MAX_CHOICE_NAME, required=True)
            if "," in label:
                raise StoreError(f'A choice name in {name} can\'t have a comma in it ("{_shown(label)}"), '
                                 f"because commas separate choices.")
            if label.casefold() in seen:
                raise StoreError(f"{name} has two choices named {label}.")
            seen.add(label.casefold())
            entries.append((entry, label))
        old_choices = old["choices"] if self._same_kind(old) and isinstance(old.get("choices"), list) else []
        old_colors = {c["id"]: c.get("color") for c in old_choices}
        out = []
        for i, ((entry, label), cid) in enumerate(zip(entries, _assign_ids(entries, old_choices, True, (), str.casefold))):
            color = entry.get("color") or old_colors.get(cid) or COLORS[i % len(COLORS)]
            if not isinstance(color, str) or color not in COLORS:
                raise StoreError(f"The color for {label} in {name} must be one of: {', '.join(COLORS)}.")
            out.append({"id": cid, "name": label, "color": color})
        return {"choices": out}

    def _check_options(self, prop, where):
        choices = prop.get("choices")
        _require(isinstance(choices, list) and choices, where, "it needs a list with at least one choice.")
        seen = set()
        for c in choices:
            _require(isinstance(c, dict) and _is_str(c.get("id")) and ID_RE.fullmatch(c["id"]) and _is_str(c.get("name")),
                     where, "each choice needs an id and a name.")
            _require(c["id"] not in seen, where, f"two choices share the id {c['id']!r}.")
            seen.add(c["id"])
            c.setdefault("color", "slate")
            _require(c["color"] in COLORS, where, f"choice {c['name']!r} has an unknown color.")

    @staticmethod
    def _lookup(prop):
        """Choice ids by id and by name ignoring case, built once per value cleaned."""
        by_id, by_name = {}, {}
        for c in prop["choices"]:
            by_id[c["id"]] = c["id"]
            by_name.setdefault(c["name"].casefold(), c["id"])
        return by_id, by_name

    @staticmethod
    def _match(prop, lookup, given, names_first):
        """The id of the choice `given` means. API input tries ids first; typed text tries names first."""
        by_id, by_name = lookup
        folded = given.strip().casefold()
        found = (by_name.get(folded) or by_id.get(folded)) if names_first else \
            (by_id.get(given) or by_name.get(folded) or by_id.get(folded))
        if found is None:
            names = ", ".join(c["name"] for c in prop["choices"])
            raise StoreError(f'{prop["name"]} has no choice called "{_shown(given.strip())}". Choices: {names}.')
        return found

    def _clean(self, value, prop):
        if not isinstance(value, str):
            raise StoreError(f"{prop['name']} must be the name or id of one of its choices.")
        return self._match(prop, self._lookup(prop), value, names_first=False)

    def _parse(self, text, prop):
        return self._match(prop, self._lookup(prop), text, names_first=True)

    def format_text(self, value, prop):
        return next((c["name"] for c in prop["choices"] if c["id"] == value), value)

    def sort_key(self, value, prop):
        return next((i for i, c in enumerate(prop["choices"]) if c["id"] == value), len(prop["choices"]))

    def _problem(self, value, prop):
        if not isinstance(value, str) or not any(c["id"] == value for c in prop["choices"]):
            return "must be the id of one of its choices"
        return None

    def _refit(self, value, prop):
        """A multi value of exactly one choice becomes that choice; a removed choice is cleared."""
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if isinstance(value, str) and self._problem(value, prop) is None:
            return value, False
        return None, True


class MultiType(ChoiceType):
    name = "multi"
    _text_limit = MAX_CHOICES * (MAX_CHOICE_NAME + 2)

    def _pick(self, prop, ids):
        if not ids:
            return None
        if len(ids) > MAX_MULTI:
            raise StoreError(f"{prop['name']} can hold at most {MAX_MULTI} choices.")
        return [c["id"] for c in prop["choices"] if c["id"] in ids]

    def _clean(self, value, prop):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise StoreError(f"{prop['name']} must be a list of its choices.")
        lookup = self._lookup(prop)
        return self._pick(prop, {self._match(prop, lookup, item, False) for item in value if item.strip()})

    def _parse(self, text, prop):
        lookup = self._lookup(prop)
        return self._pick(prop, {self._match(prop, lookup, part, True) for part in text.split(",") if part.strip()})

    def format_text(self, value, prop):
        names = {c["id"]: c["name"] for c in prop["choices"]}
        return ", ".join(names.get(cid, cid) for cid in value)

    def sort_key(self, value, prop):
        order = {c["id"]: i for i, c in enumerate(prop["choices"])}
        return tuple(order.get(cid, len(order)) for cid in value)

    def _problem(self, value, prop):
        if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
            return "must be a list of choice ids"
        ids = [c["id"] for c in prop["choices"]]
        if not set(value) <= set(ids):
            return "names a choice that doesn't exist"
        if [cid for cid in ids if cid in set(value)] != value:
            return "must list each choice once, in the order of the choices"
        if len(value) > MAX_MULTI:
            return f"has more than {MAX_MULTI} choices"
        return None

    def _refit(self, value, prop):
        """Puts choices back in order, drops removed ones, and turns a single choice into a list of one."""
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            items = value
        else:
            return None, True
        wanted = set(items)
        kept = [c["id"] for c in prop["choices"] if c["id"] in wanted][:MAX_MULTI]
        return kept or None, len(kept) < len(wanted)


class CheckboxType(PropertyType):
    name = "checkbox"
    comparisons = frozenset({"="})
    _family = "checkbox"

    def _clean(self, value, prop):
        if value is True:
            return True
        if value is False:
            return None
        raise StoreError(f"{prop['name']} must be true or false.")

    def _parse(self, text, prop):
        word = text.casefold()
        if word in _YES:
            return True
        if word in _NO:
            return None
        raise StoreError(f'{prop["name"]} must be yes or no, not "{_shown(text)}".')

    def format_text(self, value, prop):
        return "yes"

    def sort_key(self, value, prop):
        return 1

    def _problem(self, value, prop):
        return None if value is True else "must be true (a box that isn't ticked is left out)"


class UrlType(PropertyType):
    name = "url"
    _family = "words"

    def _clean(self, value, prop):
        if not isinstance(value, str):
            raise StoreError(self._wants(prop))
        link = schema.text(value, prop["name"], MAX_PROPERTY_TEXT)
        if not link:
            return None
        if not _URL.fullmatch(link):
            raise StoreError(self._wants(prop))
        return link

    _parse = _clean

    @staticmethod
    def _wants(prop):
        return f"{prop['name']} must be a link starting with http:// or https://, with no spaces."

    def sort_key(self, value, prop):
        return value.casefold()

    def _problem(self, value, prop):
        if not isinstance(value, str) or len(value) > MAX_PROPERTY_TEXT or not _URL.fullmatch(value):
            return f"must be a link starting with http:// or https://, with no spaces, at most {MAX_PROPERTY_TEXT} characters"
        return None


class RatingType(PropertyType):
    name = "rating"
    comparisons = _ORDERED
    _family = "number"

    def _clean_options(self, raw, name, old):
        top = self._option(raw, "max", old, DEFAULT_RATING_MAX)
        if top is None or top == "":
            top = DEFAULT_RATING_MAX
        low, high = RATING_MAX_RANGE
        if not _is_int(top) or not low <= top <= high:
            raise StoreError(f"The highest rating for {name} must be a whole number from {low} to {high}.")
        return {"max": top}

    def _check_options(self, prop, where):
        low, high = RATING_MAX_RANGE
        prop.setdefault("max", DEFAULT_RATING_MAX)
        _require(_is_int(prop["max"]) and low <= prop["max"] <= high, where, f"max must be a whole number from {low} to {high}.")

    def _wants(self, prop):
        return f"{prop['name']} must be a whole number from 1 to {prop['max']}."

    def _clean(self, value, prop):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if self._problem(value, prop) is not None:
            raise StoreError(self._wants(prop))
        return value

    def _parse(self, text, prop):
        m = _RATING_TEXT.fullmatch(text)
        if not m:
            raise StoreError(self._wants(prop))
        if m.group(2) is not None and int(m.group(2)) != prop["max"]:
            raise StoreError(f"{prop['name']} is rated out of {prop['max']}, not {int(m.group(2))}.")
        return self._clean(int(m.group(1)), prop)

    def format_text(self, value, prop):
        return f"{value}/{prop['max']}"

    def _problem(self, value, prop):
        if not _is_int(value) or not 1 <= value <= prop["max"]:
            return f"must be a whole number from 1 to {prop['max']}"
        return None


TYPES = {kind.name: kind for kind in (TextType(), NumberType(), ChoiceType(), MultiType(), CheckboxType(), UrlType(),
                                      RatingType())}


# ---- a board's properties ------------------------------------------------------------------------


def find_property(board, name_or_id, names_first=False):
    """The property with this id, or else this name ignoring case, spaces and hyphens. None if there's none.

    An exact id wins over a name, so ids sent by the page always mean the same property even after
    renames; `story-points` and `Story Points` both find a property named "Story points". With
    `names_first`, for what a person types, a name wins: a property renamed from Size keeps the id
    size, and "size" then means the property called Size, not that one.
    """
    if not isinstance(name_or_id, str):
        return None
    props = board.get("properties") or []
    key = _name_key(name_or_id)
    if names_first and key:
        for prop in props:
            if _name_key(prop["name"]) == key:
                return prop
    for prop in props:
        if prop["id"] == name_or_id:
            return prop
    if not key:
        return None
    for field in ("name", "id"):
        for prop in props:
            if _name_key(prop[field]) == key:
                return prop
    return None


def property_text(board, card, prop):
    """The card's value for `prop` (a definition, or a name or id) as text; "" when it's unset."""
    if not isinstance(prop, dict):
        prop = find_property(board, prop)
        if prop is None:
            return ""
    value = (card.get("properties") or {}).get(prop["id"])
    return "" if value is None else TYPES[prop["type"]].format_text(value, prop)


def clean_definitions(board, raw_list, keep_ids=False):
    """A whole new list of property definitions for `board`, cleaned like columns are. Returns the list.

    Entries that send an id already on the board keep it, so renaming keeps every card's value. An
    entry with no usable id that has the name of an existing property is that property. New entries
    get an id made from the name, unique on the board and never one of `RESERVED_IDS`. With `keep_ids`
    (imports), any well formed id is kept. Names must differ ignoring case, spaces and hyphens, so
    `find_property` is never ambiguous. Type changes follow `PropertyType.clean_definition`. Doesn't
    change `board`: set board["properties"] to the result, then call `prune_values(board)`.
    """
    if not isinstance(raw_list, list):
        raise StoreError("Properties must be a list.")
    if len(raw_list) > MAX_PROPERTIES:
        raise StoreError(f"A board can have at most {MAX_PROPERTIES} properties.")
    old_list = board.get("properties") or []
    old_by_id = {p["id"]: p for p in old_list}
    entries, seen = [], set()
    for raw in raw_list:
        if not isinstance(raw, dict):
            raise StoreError("Each property needs a name and a type.")
        given = raw.get("id")
        before = old_by_id.get(given) if isinstance(given, str) else None
        name = schema.text(_given(raw, "name", before, None), "Property name", MAX_PROPERTY_NAME, required=True)
        if _name_key(name) in seen:
            raise StoreError(f"There are two properties named {name}.")
        seen.add(_name_key(name))
        entries.append((raw, name))
    out = []
    for (raw, name), pid in zip(entries, _assign_ids(entries, old_list, keep_ids, RESERVED_IDS, _name_key)):
        old = old_by_id.get(pid)
        kind = _given(raw, "type", old, None)
        if kind is None or kind == "":
            raise StoreError(f"{name} needs a type: {', '.join(TYPE_NAMES)}.")
        if not isinstance(kind, str) or kind not in TYPES:
            raise StoreError(f'There\'s no property type called "{_shown(str(kind))}". Types: {", ".join(TYPE_NAMES)}.')
        values = ()
        if old is not None and old.get("type") != kind:
            values = [c["properties"][pid] for c in board.get("cards") or []
                      if isinstance(c.get("properties"), dict) and pid in c["properties"]]
        prop = TYPES[kind].clean_definition({**raw, "name": name}, old, values)
        prop["id"] = pid
        out.append(prop)
    return out


def check_definitions(board, where):
    """File check for board["properties"] and every card's values. Raises BadFile naming the problem.

    Fills in what is missing (no properties, a card with no values, options left out) and never changes
    a value that is there. Run it after the cards themselves are checked. Works on anything with
    `properties` and optional `cards`, such as a saved template.
    """
    props = board.setdefault("properties", [])
    _require(isinstance(props, list), where, "properties must be a list.")
    by_id = {}
    for prop in props:
        _require(isinstance(prop, dict), where, "each property needs an id, a name and a type.")
        kind = prop.get("type")
        _require(_is_str(kind) and kind in TYPES, where,
                 f"property {prop.get('name')!r} has no known type (the types are {', '.join(TYPE_NAMES)}).")
        TYPES[kind].check_definition(prop, where)
        _require(prop["id"] not in by_id, where, f"two properties share the id {prop['id']!r}.")
        by_id[prop["id"]] = prop
    for card in board.get("cards") or []:
        _require(isinstance(card, dict), where, "each card must be an object.")
        at = f"{where}, card {card.get('key')}"
        values = card.setdefault("properties", {})
        _require(isinstance(values, dict), at, "properties must map property ids to values.")
        for pid, value in values.items():
            prop = by_id.get(pid)
            _require(prop is not None, at, f"it has a value for a property that doesn't exist ({pid!r}).")
            TYPES[prop["type"]].check_value(value, prop, at)


def prune_values(board):
    """Make every card's values fit the board's current definitions. Returns how many values lost something.

    Values for removed properties are dropped, as are values for removed choices and values outside a
    new smallest, largest or highest rating. A multi value loses only the removed choices; numbers are
    rounded to fewer decimal places. Each value cleared or cut down counts once. Putting a multi value
    back in choice order, or converting it after an allowed type change, isn't counted.

    Never edits a card in place: a changed card is replaced by a new dict in a new board["cards"] list,
    so a board shared elsewhere stays as it was. When nothing changes, board["cards"] isn't touched.
    """
    by_id = {p["id"]: p for p in board.get("properties") or []}
    cards, dropped, changed = [], 0, False
    for card in board.get("cards") or []:
        values = card.get("properties")
        if not isinstance(values, dict) or not values:
            cards.append(card)
            continue
        kept, touched = {}, False
        for pid, value in values.items():
            prop = by_id.get(pid)
            if prop is None:
                dropped, touched = dropped + 1, True
                continue
            kind = TYPES[prop["type"]]
            if kind._problem(value, prop) is None:
                kept[pid] = value
                continue
            fitted, lost = kind._refit(value, prop)
            dropped, touched = dropped + int(lost), True
            if fitted is not None:
                kept[pid] = fitted
        cards.append({**card, "properties": kept} if touched else card)
        changed = changed or touched
    if changed:
        board["cards"] = cards
    return dropped
