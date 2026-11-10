"""The property type registry: definitions, values, text forms, sorting and the board-level helpers."""
import copy
import json
import unittest

from laneboard import properties as P
from laneboard import schema
from laneboard.schema import BadFile, StoreError

T = P.TYPES


def define(*raw):
    """Definitions cleaned onto an empty board, the way a new board gets them."""
    return P.clean_definitions({"properties": [], "cards": []}, list(raw))


def one(**raw):
    return define(raw)[0]


def board(props, *values):
    """A board with these definitions and one card per values dict (keys T-1, T-2, ...)."""
    cards = [{"key": f"T-{i}", "title": f"Card {i}", "properties": dict(v)} for i, v in enumerate(values, 1)]
    return {"name": "Test", "properties": props, "cards": cards}


def stored(value):
    """What a value looks like after a trip through the file."""
    return json.loads(json.dumps(value, allow_nan=False))


class Registry(unittest.TestCase):
    def test_one_instance_per_type_in_order(self):
        self.assertEqual(P.TYPE_NAMES, ("text", "number", "choice", "multi", "checkbox", "url", "rating"))
        self.assertEqual(tuple(T), P.TYPE_NAMES)
        for name, kind in T.items():
            self.assertIsInstance(kind, P.PropertyType)
            self.assertEqual(kind.name, name)
            self.assertIsInstance(kind.comparisons, frozenset)
            self.assertLessEqual(kind.comparisons, {"=", "<", "<=", ">", ">=", ".."})

    def test_comparisons(self):
        ordered = {"=", "<", "<=", ">", ">=", ".."}
        self.assertEqual(T["number"].comparisons, ordered)
        self.assertEqual(T["rating"].comparisons, ordered)
        for name in ("choice", "multi", "checkbox"):
            self.assertEqual(T[name].comparisons, {"="})
        for name in ("text", "url"):
            self.assertEqual(T[name].comparisons, frozenset())

    def test_limits_are_importable_from_both_modules(self):
        self.assertEqual((P.MAX_PROPERTIES, P.MAX_CHOICES, P.MAX_MULTI, P.MAX_PROPERTY_NAME), (30, 50, 20, 40))
        for name in ("MAX_PROPERTIES", "MAX_CHOICES", "MAX_MULTI", "MAX_PROPERTY_NAME"):
            self.assertIs(getattr(P, name), getattr(schema, name))

    def test_clean_values_always_pass_the_file_check(self):
        props = define({"name": "Note", "type": "text"}, {"name": "Points", "type": "number", "decimals": 2, "unit": "h"},
                       {"name": "Priority", "type": "choice", "choices": ["Low", "High"]},
                       {"name": "Platform", "type": "multi", "choices": ["iOS", "Web"]},
                       {"name": "Done", "type": "checkbox"}, {"name": "Link", "type": "url"},
                       {"name": "Stars", "type": "rating", "max": 10})
        inputs = {"text": [" hi\tthere ", "x" * 500, "café \ud83d"], "number": [2.675, 1.005, 3, -0.0, 1e15, 0.1 + 0.2],
                  "choice": ["low", "HIGH"], "multi": [["web", "iOS", "web"]], "checkbox": [True],
                  "url": ["  HTTPS://Example.com/a?b=c  "], "rating": [1, 10, 7.0]}
        for prop in props:
            kind = T[prop["type"]]
            for raw in inputs[prop["type"]]:
                value = kind.clean_value(raw, prop)
                self.assertEqual(stored(value), value)
                kind.check_value(stored(value), prop, "here")
                self.assertEqual(kind.parse_text(kind.format_text(value, prop), prop), value, (prop["name"], raw))
                kind.sort_key(value, prop)

    def test_hostile_values_give_plain_errors(self):
        props = define({"name": "Note", "type": "text"}, {"name": "Points", "type": "number"},
                       {"name": "Priority", "type": "choice", "choices": ["Low"]},
                       {"name": "Platform", "type": "multi", "choices": ["iOS"]},
                       {"name": "Done", "type": "checkbox"}, {"name": "Link", "type": "url"}, {"name": "Stars", "type": "rating"})
        junk = [{"a": 1}, [[1]], [{"x": 1}], 10 ** 400, float("nan"), float("inf"), True, "x" * 100000, [None], 3.5, "1"]
        for prop in props:
            kind = T[prop["type"]]
            for value in junk:
                try:
                    out = kind.clean_value(value, prop)
                except StoreError as e:
                    self.assertIn(prop["name"], str(e))
                else:
                    kind.check_value(out, prop, "here")
                if not isinstance(value, str) and kind.name not in ("number", "checkbox"):
                    with self.assertRaises(BadFile):
                        kind.check_value(value, prop, "here")
            for text in ("", "   "):
                self.assertIsNone(kind.parse_text(text, prop))
            with self.assertRaises(StoreError):
                kind.parse_text(42, prop)


class Definitions(unittest.TestCase):
    def test_common_fields(self):
        prop = one(name="  Story   points ", type="text", onCard=True, extra="dropped")
        self.assertEqual(prop, {"id": "story-points", "name": "Story   points", "type": "text", "onCard": True})
        self.assertFalse(one(name="Note", type="text")["onCard"])
        for raw, message in (({"type": "text"}, "Property name can't be empty."),
                             ({"name": "x" * 41, "type": "text"}, "Property name is too long (41 characters; the limit is 40)."),
                             ({"name": "Note", "type": "text", "onCard": "yes"}, "Whether Note shows on cards must be true or false.")):
            with self.assertRaises(StoreError) as caught:
                define(raw)
            self.assertEqual(str(caught.exception), message)
        with self.assertRaises(StoreError):
            T["text"].clean_definition("Note")

    def test_number_options(self):
        prop = one(name="Hours", type="number", min=0, max=40.0, decimals=2, unit=" h ")
        self.assertEqual({k: prop[k] for k in ("min", "max", "decimals", "unit")}, {"min": 0, "max": 40, "decimals": 2, "unit": "h"})
        self.assertIsInstance(prop["max"], int)
        plain = one(name="Size", type="number")
        self.assertEqual({k: plain[k] for k in ("min", "max", "decimals", "unit")}, {"min": None, "max": None, "decimals": 0, "unit": ""})
        cleared = one(name="Size", type="number", min="", max=None, decimals=None)
        self.assertEqual((cleared["min"], cleared["max"], cleared["decimals"]), (None, None, 0))
        bad = [({"min": "1"}, "The smallest value for Size must be a number, or empty."),
               ({"max": True}, "The largest value for Size must be a number, or empty."),
               ({"max": float("nan")}, "The largest value for Size must be a number, or empty."),
               ({"min": 10 ** 16}, "The smallest value for Size must be from -1000000000000000 to 1000000000000000."),
               ({"min": 5, "max": 1}, "The smallest value for Size can't be more than the largest."),
               ({"decimals": 5}, "Decimal places for Size must be a whole number from 0 to 4."),
               ({"decimals": 1.0}, "Decimal places for Size must be a whole number from 0 to 4."),
               ({"decimals": True}, "Decimal places for Size must be a whole number from 0 to 4."),
               ({"unit": "x" * 11}, "The unit for Size is too long (11 characters; the limit is 10)."),
               ({"unit": 3}, "The unit for Size must be text.")]
        for options, message in bad:
            with self.assertRaises(StoreError) as caught:
                one(name="Size", type="number", **options)
            self.assertEqual(str(caught.exception), message, options)

    def test_choice_options(self):
        prop = one(name="Priority", type="choice", choices=["Low", {"name": " Medium ", "color": "rose"}, {"name": "High!"}])
        self.assertEqual(prop["choices"], [{"id": "low", "name": "Low", "color": "blue"},
                                           {"id": "medium", "name": "Medium", "color": "rose"},
                                           {"id": "high", "name": "High!", "color": "violet"}])
        many = one(name="Many", type="multi", choices=[f"C{i}" for i in range(50)])
        self.assertEqual([c["color"] for c in many["choices"][:8]], list(schema.COLORS) + ["blue"])
        self.assertEqual(len({c["id"] for c in many["choices"]}), 50)
        same_slug = one(name="Odd", type="choice", choices=["A b", "A-b", "a_b"])
        self.assertEqual([c["id"] for c in same_slug["choices"]], ["a-b", "a-b-2", "a-b-3"])
        bad = [({}, "Priority needs at least one choice."),
               ({"choices": []}, "Priority needs at least one choice."),
               ({"choices": "Low, High"}, "The choices for Priority must be a list."),
               ({"choices": [f"C{i}" for i in range(51)]}, "Priority can have at most 50 choices."),
               ({"choices": ["Low", "low"]}, "Priority has two choices named low."),
               ({"choices": [""]}, "A choice name in Priority can't be empty."),
               ({"choices": [3]}, "Each choice for Priority needs a name."),
               ({"choices": ["x" * 41]}, "A choice name in Priority is too long (41 characters; the limit is 40)."),
               ({"choices": ["Low, mild"]}, 'A choice name in Priority can\'t have a comma in it ("Low, mild"), because commas separate choices.'),
               ({"choices": [{"name": "Low", "color": "pink"}]},
                "The color for Low in Priority must be one of: blue, teal, violet, amber, rose, green, slate.")]
        for options, message in bad:
            for kind in ("choice", "multi"):
                with self.assertRaises(StoreError) as caught:
                    one(name="Priority", type=kind, **options)
                self.assertEqual(str(caught.exception), message, options)

    def test_choice_ids_stay_put(self):
        old = one(name="Priority", type="choice", choices=["Low", "High"])
        renamed = T["choice"].clean_definition({"name": "Priority", "choices": [
            {"id": "high", "name": "Top"}, {"id": "low", "name": "Bottom", "color": "green"}, {"name": "High"}]}, old)
        self.assertEqual(renamed["choices"], [{"id": "high", "name": "Top", "color": "teal"},
                                              {"id": "low", "name": "Bottom", "color": "green"},
                                              {"id": "high-2", "name": "High", "color": "violet"}])
        by_name = T["choice"].clean_definition({"name": "Priority", "choices": ["high", "Medium"]}, old)
        self.assertEqual([c["id"] for c in by_name["choices"]], ["high", "medium"])
        # "Low" is removed and a new "Low!" added in the same change: it mustn't pick up Low's id (or its values).
        replaced = T["choice"].clean_definition({"name": "Priority", "choices": [{"id": "high", "name": "High"}, "Low!"]}, old)
        self.assertEqual([c["id"] for c in replaced["choices"]], ["high", "low-2"])
        fresh = T["choice"].clean_definition({"name": "Priority", "choices": [{"id": "high", "name": "High"}, {"name": "Lowest", "id": "nope!"}]}, old)
        self.assertEqual([c["id"] for c in fresh["choices"]], ["high", "lowest"])
        twice = T["choice"].clean_definition({"name": "Priority", "choices": [{"id": "low", "name": "A"}, {"id": "low", "name": "B"}]}, old)
        self.assertEqual([c["id"] for c in twice["choices"]], ["low", "b"])
        imported = one(name="Priority", type="choice", choices=[{"id": "p1", "name": "One"}, {"id": "Bad Id", "name": "Two"}])
        self.assertEqual([c["id"] for c in imported["choices"]], ["p1", "two"])

    def test_options_left_out_keep_their_old_values(self):
        old = one(name="Hours", type="number", min=0, max=8, decimals=1, unit="h", onCard=True)
        kept = T["number"].clean_definition({"name": "Time"}, old)
        self.assertEqual(kept, {**old, "name": "Time"})
        cleared = T["number"].clean_definition({"name": "Time", "max": None}, old)
        self.assertIsNone(cleared["max"])
        multi = T["multi"].clean_definition({"name": "P"}, one(name="P", type="choice", choices=["A", "B"]))
        self.assertEqual([c["id"] for c in multi["choices"]], ["a", "b"])
        rating = T["rating"].clean_definition({"name": "Hours"}, old)
        self.assertEqual(rating["max"], 5)  # a number's max is not a rating's max

    def test_rating_options(self):
        self.assertEqual(one(name="Stars", type="rating")["max"], 5)
        self.assertEqual(one(name="Stars", type="rating", max=None)["max"], 5)
        for top in (3, 10):
            self.assertEqual(one(name="Stars", type="rating", max=top)["max"], top)
        for top in (2, 11, 4.0, True, "5"):
            with self.assertRaises(StoreError) as caught:
                one(name="Stars", type="rating", max=top)
            self.assertEqual(str(caught.exception), "The highest rating for Stars must be a whole number from 3 to 10.")

    def test_types_without_options_store_none(self):
        for kind in ("text", "checkbox", "url"):
            self.assertEqual(set(one(name="X", type=kind, min=1, choices=["a"], max=3)), {"id", "name", "type", "onCard"})


class TextValues(unittest.TestCase):
    prop = {"id": "note", "name": "Note", "type": "text", "onCard": False}

    def test_clean_and_parse(self):
        for kind_method in (T["text"].clean_value, T["text"].parse_text):
            self.assertEqual(kind_method("  two\nlines\x00 ", self.prop), "two lines")
            self.assertIsNone(kind_method(" \n ", self.prop))
            self.assertEqual(kind_method("x" * 500, self.prop), "x" * 500)
            with self.assertRaises(StoreError) as caught:
                kind_method("x" * 501, self.prop)
            self.assertEqual(str(caught.exception), "Note is too long (501 characters; the limit is 500).")
        self.assertIsNone(T["text"].clean_value(None, self.prop))
        with self.assertRaises(StoreError) as caught:
            T["text"].clean_value(5, self.prop)
        self.assertEqual(str(caught.exception), "Note must be text.")

    def test_format_and_sort(self):
        self.assertEqual(T["text"].format_text("Hello", self.prop), "Hello")
        self.assertEqual(sorted(["b", "A", "c"], key=lambda v: T["text"].sort_key(v, self.prop)), ["A", "b", "c"])

    def test_file_check(self):
        T["text"].check_value("fine", self.prop, "card T-1")
        for value in ("", " padded", "two\nlines", "x" * 501, 5, None, ["a"]):
            with self.assertRaises(BadFile) as caught:
                T["text"].check_value(value, self.prop, "card T-1")
            self.assertTrue(str(caught.exception).startswith("card T-1: the value of 'Note' "), caught.exception)


class NumberValues(unittest.TestCase):
    def setUp(self):
        self.points = one(name="Points", type="number", min=0, max=100, unit="pts")
        self.hours = one(name="Hours", type="number", decimals=2)
        self.n = T["number"]

    def test_clean_rounds_half_up(self):
        self.assertEqual(self.n.clean_value(2.5, self.points), 3)
        self.assertIsInstance(self.n.clean_value(2.5, self.points), int)
        self.assertEqual(self.n.clean_value(2.675, self.hours), 2.68)
        self.assertEqual(self.n.clean_value(1.005, self.hours), 1.01)
        self.assertEqual(self.n.clean_value(-2.5, self.hours), -2.5)
        self.assertEqual(self.n.clean_value(-1.005, self.hours), -1.01)
        self.assertEqual(self.n.clean_value(0.1 + 0.2, self.hours), 0.3)
        self.assertIsInstance(self.n.clean_value(3.0, self.hours), int)
        self.assertEqual(repr(self.n.clean_value(-0.0, self.hours)), "0")
        self.assertEqual(self.n.clean_value(0, self.points), 0)
        self.assertEqual(self.n.clean_value(10 ** 15, self.hours), 10 ** 15)
        self.assertIsNone(self.n.clean_value("", self.points))
        self.assertIsNone(self.n.clean_value(None, self.points))

    def test_clean_refuses(self):
        for value in (True, False, "3", float("nan"), [3], {"v": 3}):
            with self.assertRaises(StoreError) as caught:
                self.n.clean_value(value, self.points)
            self.assertEqual(str(caught.exception), "Points must be a number from 0 to 100.")
        for value in (-1, 100.5, 101):
            with self.assertRaises(StoreError) as caught:
                self.n.clean_value(value, self.points)
            self.assertEqual(str(caught.exception), "Points must be a number from 0 to 100.")
        self.assertEqual(self.n.clean_value(-0.4, self.points), 0)  # rounds into the range first
        with self.assertRaises(StoreError) as caught:
            self.n.clean_value("3", self.hours)
        self.assertEqual(str(caught.exception), "Hours must be a number.")
        for value in (float("inf"), float("-inf"), 10 ** 15 + 1, 10 ** 400):
            with self.assertRaises(StoreError) as caught:
                self.n.clean_value(value, self.hours)
            self.assertEqual(str(caught.exception), "Hours must be a number from -1000000000000000 to 1000000000000000.")

    def test_range_messages(self):
        for value, options, message in ((-1, {"min": 1}, "Size must be a number of 1 or more."),
                                        (2.6, {"max": 2.5, "decimals": 1}, "Size must be a number of 2.5 or less."),
                                        (-10 ** 7, {"min": -0.5, "max": 1e6, "decimals": 1}, "Size must be a number from -0.5 to 1000000.")):
            with self.assertRaises(StoreError) as caught:
                self.n.clean_value(value, one(name="Size", type="number", **options))
            self.assertEqual(str(caught.exception), message)

    def test_parse_text(self):
        for text, value in (("3", 3), ("2.5", 3), ("3 pts", 3), ("3pts", 3), ("3 PTS", 3), ("+4", 4), ("0", 0),
                            (".5", 1), ("5.", 5), ("007", 7), (" 9 ", 9)):
            self.assertEqual(self.n.parse_text(text, self.points), value, text)
        self.assertEqual(self.n.parse_text("-2.5", self.hours), -2.5)
        self.assertEqual(self.n.parse_text("1.239", self.hours), 1.24)
        self.assertIsNone(self.n.parse_text("", self.points))
        with self.assertRaises(StoreError) as caught:
            self.n.parse_text("1,200", self.hours)
        self.assertEqual(str(caught.exception), "Hours must be a number written without commas, like 1200 or 2.5.")
        for text in ("abc", "pts", "3 points", "1e3", "inf", "nan", "3 h", "٣", "--3", "3..5", "0x10"):
            with self.assertRaises(StoreError) as caught:
                self.n.parse_text(text, self.points)
            self.assertEqual(str(caught.exception), "Points must be a number from 0 to 100.", text)
        with self.assertRaises(StoreError) as caught:
            self.n.parse_text("3 pts", self.hours)  # no unit on this one
        self.assertEqual(str(caught.exception), "Hours must be a number.")
        with self.assertRaises(StoreError) as caught:
            self.n.parse_text("9" * 400, self.hours)
        self.assertEqual(str(caught.exception), "Hours must be a number from -1000000000000000 to 1000000000000000.")

    def test_format_and_sort(self):
        self.assertEqual(self.n.format_text(3, self.points), "3 pts")
        self.assertEqual(self.n.format_text(2.5, self.hours), "2.5")
        self.assertEqual(self.n.format_text(0.0001, one(name="Tiny", type="number", decimals=4)), "0.0001")
        self.assertEqual(self.n.format_text(10 ** 15, self.hours), "1000000000000000")
        self.assertEqual(self.n.format_text(1e15, self.hours), "1000000000000000")
        values = [10, -1.5, 2, 0]
        self.assertEqual(sorted(values, key=lambda v: self.n.sort_key(v, self.hours)), [-1.5, 0, 2, 10])

    def test_file_check(self):
        self.n.check_value(2.25, self.hours, "card T-1")
        for value, problem in ((True, "must be a number"), ("3", "must be a number"), (None, "must be a number"),
                               (10 ** 16, "must be a number"), (2.125, "has more than 2 decimal places"),
                               (-1, "must be a number from 0 to 100")):
            prop = self.points if value == -1 else self.hours
            with self.assertRaises(BadFile) as caught:
                self.n.check_value(value, prop, "card T-1")
            self.assertEqual(str(caught.exception), f"card T-1: the value of {prop['name']!r} {problem}.")
        with self.assertRaises(BadFile) as caught:
            self.n.check_value(1.5, one(name="Size", type="number", decimals=1, max=1), "here")
        self.assertEqual(str(caught.exception), "here: the value of 'Size' must be a number of 1 or less.")
        with self.assertRaises(BadFile) as caught:
            self.n.check_value(1.25, one(name="Size", type="number", decimals=1), "here")
        self.assertEqual(str(caught.exception), "here: the value of 'Size' has more than 1 decimal place.")


class ChoiceValues(unittest.TestCase):
    def setUp(self):
        self.priority = one(name="Priority", type="choice", choices=["Low", "Medium", "High"])
        self.c = T["choice"]

    def test_clean_and_parse_take_ids_or_names(self):
        for method in (self.c.clean_value, self.c.parse_text):
            for given in ("high", "High", "HIGH", " high ", "MEDIUM"):
                self.assertIn(method(given, self.priority), ("high", "medium"))
            with self.assertRaises(StoreError) as caught:
                method("hihg", self.priority)
            self.assertEqual(str(caught.exception), 'Priority has no choice called "hihg". Choices: Low, Medium, High.')
        self.assertIsNone(self.c.clean_value("", self.priority))
        self.assertIsNone(self.c.parse_text("", self.priority))
        with self.assertRaises(StoreError) as caught:
            self.c.clean_value(2, self.priority)
        self.assertEqual(str(caught.exception), "Priority must be the name or id of one of its choices.")
        with self.assertRaises(StoreError) as caught:
            self.c.clean_value("x" * 100, self.priority)
        self.assertIn('"' + "x" * 57 + '..."', str(caught.exception))

    def test_ids_win_from_the_api_and_names_win_from_typed_text(self):
        swapped = T["choice"].clean_definition({"name": "Size", "choices": [{"id": "small", "name": "Big"},
                                                                          {"id": "big", "name": "Small"}]})
        self.assertEqual(self.c.clean_value("big", swapped), "big")
        self.assertEqual(self.c.parse_text("big", swapped), "small")
        self.assertEqual(self.c.parse_text("Big", swapped), "small")

    def test_format_sort_and_file_check(self):
        self.assertEqual(self.c.format_text("medium", self.priority), "Medium")
        self.assertEqual(sorted(["high", "low", "medium"], key=lambda v: self.c.sort_key(v, self.priority)), ["low", "medium", "high"])
        self.c.check_value("low", self.priority, "here")
        for value in ("Low", "gone", 1, ["low"], None):
            with self.assertRaises(BadFile) as caught:
                self.c.check_value(value, self.priority, "here")
            self.assertEqual(str(caught.exception), "here: the value of 'Priority' must be the id of one of its choices.")


class MultiValues(unittest.TestCase):
    def setUp(self):
        self.platform = one(name="Platform", type="multi", choices=["iOS", "Android", "Web"])
        self.m = T["multi"]

    def test_clean_keeps_choice_order_without_repeats(self):
        self.assertEqual(self.m.clean_value(["web", "IOS", "web", "ios"], self.platform), ["ios", "web"])
        self.assertEqual(self.m.clean_value(["Android"], self.platform), ["android"])
        for empty in ([], [" "], "", None):
            self.assertIsNone(self.m.clean_value(empty, self.platform))
        for value in ("web", [1], [["web"]], {"web": True}, True):
            with self.assertRaises(StoreError) as caught:
                self.m.clean_value(value, self.platform)
            self.assertEqual(str(caught.exception), "Platform must be a list of its choices.")
        with self.assertRaises(StoreError) as caught:
            self.m.clean_value(["web", "desktop"], self.platform)
        self.assertEqual(str(caught.exception), 'Platform has no choice called "desktop". Choices: iOS, Android, Web.')

    def test_at_most_twenty(self):
        big = one(name="Tags", type="multi", choices=[f"T{i}" for i in range(30)])
        ids = [c["id"] for c in big["choices"]]
        self.assertEqual(self.m.clean_value(ids[:20], big), ids[:20])
        with self.assertRaises(StoreError) as caught:
            self.m.clean_value(ids[:21], big)
        self.assertEqual(str(caught.exception), "Tags can hold at most 20 choices.")
        with self.assertRaises(StoreError):
            self.m.parse_text(",".join(ids[:21]), big)
        self.assertEqual(self.m.parse_text(", ".join(c["name"] for c in big["choices"][:20]), big), ids[:20])

    def test_parse_text(self):
        self.assertEqual(self.m.parse_text("Web, iOS", self.platform), ["ios", "web"])
        self.assertEqual(self.m.parse_text("web,,android, ", self.platform), ["android", "web"])
        self.assertIsNone(self.m.parse_text(" , ", self.platform))
        with self.assertRaises(StoreError) as caught:
            self.m.parse_text("Web, Desktop", self.platform)
        self.assertEqual(str(caught.exception), 'Platform has no choice called "Desktop". Choices: iOS, Android, Web.')

    def test_format_sort_and_file_check(self):
        self.assertEqual(self.m.format_text(["ios", "web"], self.platform), "iOS, Web")
        values = [["web"], ["ios", "web"], ["ios"], ["android"]]
        self.assertEqual(sorted(values, key=lambda v: self.m.sort_key(v, self.platform)), [["ios"], ["ios", "web"], ["android"], ["web"]])
        self.m.check_value(["ios", "web"], self.platform, "here")
        for value, problem in (([], "must be a list of choice ids"), ("ios", "must be a list of choice ids"),
                               ([1], "must be a list of choice ids"), (["desktop"], "names a choice that doesn't exist"),
                               (["web", "ios"], "must list each choice once, in the order of the choices"),
                               (["ios", "ios"], "must list each choice once, in the order of the choices")):
            with self.assertRaises(BadFile) as caught:
                self.m.check_value(value, self.platform, "here")
            self.assertEqual(str(caught.exception), f"here: the value of 'Platform' {problem}.")
        big = one(name="Tags", type="multi", choices=[f"T{i}" for i in range(30)])
        with self.assertRaises(BadFile) as caught:
            self.m.check_value([c["id"] for c in big["choices"][:21]], big, "here")
        self.assertEqual(str(caught.exception), "here: the value of 'Tags' has more than 20 choices.")


class CheckboxValues(unittest.TestCase):
    prop = {"id": "reviewed", "name": "Reviewed", "type": "checkbox", "onCard": False}

    def test_only_true_is_stored(self):
        cb = T["checkbox"]
        self.assertIs(cb.clean_value(True, self.prop), True)
        self.assertIsNone(cb.clean_value(False, self.prop))
        self.assertIsNone(cb.clean_value(None, self.prop))
        for value in (1, 0, "yes", "true", [True]):
            with self.assertRaises(StoreError) as caught:
                cb.clean_value(value, self.prop)
            self.assertEqual(str(caught.exception), "Reviewed must be true or false.")

    def test_parse_text(self):
        cb = T["checkbox"]
        for text in ("yes", "YES", "true", "True", "1", "y", "Y"):
            self.assertIs(cb.parse_text(text, self.prop), True, text)
        for text in ("no", "No", "false", "FALSE", "0", "n", ""):
            self.assertIsNone(cb.parse_text(text, self.prop), text)
        with self.assertRaises(StoreError) as caught:
            cb.parse_text("maybe", self.prop)
        self.assertEqual(str(caught.exception), 'Reviewed must be yes or no, not "maybe".')

    def test_format_sort_and_file_check(self):
        cb = T["checkbox"]
        self.assertEqual(cb.format_text(True, self.prop), "yes")
        self.assertEqual(cb.sort_key(True, self.prop), 1)
        cb.check_value(True, self.prop, "here")
        for value in (False, 1, "yes", None):
            with self.assertRaises(BadFile) as caught:
                cb.check_value(value, self.prop, "here")
            self.assertEqual(str(caught.exception), "here: the value of 'Reviewed' must be true (a box that isn't ticked is left out).")


class UrlValues(unittest.TestCase):
    prop = {"id": "link", "name": "Link", "type": "url", "onCard": False}

    def test_clean_and_parse(self):
        for method in (T["url"].clean_value, T["url"].parse_text):
            for link in ("https://example.com", "http://example.com/a?b=c#d", "HTTPS://EXAMPLE.COM", "https://x.test/café"):
                self.assertEqual(method(link, self.prop), link)
            self.assertEqual(method("  https://example.com  ", self.prop), "https://example.com")
            self.assertIsNone(method("  ", self.prop))
            for link in ("ftp://example.com", "javascript:alert(1)", "example.com", "https://", "https://a b",
                         "https://a\x00b", "https://a\nb", " data:text/html,x", "https://a b"):
                with self.assertRaises(StoreError) as caught:
                    method(link, self.prop)
                self.assertEqual(str(caught.exception), "Link must be a link starting with http:// or https://, with no spaces.", link)
            with self.assertRaises(StoreError) as caught:
                method("https://" + "a" * 493, self.prop)
            self.assertEqual(str(caught.exception), "Link is too long (501 characters; the limit is 500).")
        with self.assertRaises(StoreError):
            T["url"].clean_value(["https://example.com"], self.prop)

    def test_format_sort_and_file_check(self):
        u = T["url"]
        self.assertEqual(u.format_text("https://example.com", self.prop), "https://example.com")
        self.assertEqual(sorted(["https://b.test", "HTTPS://A.test"], key=lambda v: u.sort_key(v, self.prop)), ["HTTPS://A.test", "https://b.test"])
        u.check_value("https://example.com", self.prop, "here")
        for value in ("javascript:alert(1)", "https://a\x00b", "https://a\ud800", "https://" + "a" * 493, 5, ""):
            with self.assertRaises(BadFile):
                u.check_value(value, self.prop, "here")


class RatingValues(unittest.TestCase):
    def setUp(self):
        self.stars = one(name="Confidence", type="rating")
        self.r = T["rating"]

    def test_clean(self):
        self.assertEqual(self.r.clean_value(4, self.stars), 4)
        self.assertEqual(self.r.clean_value(5.0, self.stars), 5)
        self.assertIsInstance(self.r.clean_value(5.0, self.stars), int)
        self.assertIsNone(self.r.clean_value(None, self.stars))
        for value in (0, 6, 4.5, True, "4", float("nan"), float("inf"), [4]):
            with self.assertRaises(StoreError) as caught:
                self.r.clean_value(value, self.stars)
            self.assertEqual(str(caught.exception), "Confidence must be a whole number from 1 to 5.")

    def test_parse_text(self):
        for text in ("4", "4/5", "4 / 5", " 04/5 "):
            self.assertEqual(self.r.parse_text(text, self.stars), 4, text)
        with self.assertRaises(StoreError) as caught:
            self.r.parse_text("4/10", self.stars)
        self.assertEqual(str(caught.exception), "Confidence is rated out of 5, not 10.")
        for text in ("6", "0", "x", "4/", "/5", "4.5", "-1", "four"):
            with self.assertRaises(StoreError) as caught:
                self.r.parse_text(text, self.stars)
            self.assertEqual(str(caught.exception), "Confidence must be a whole number from 1 to 5.", text)
        ten = one(name="Score", type="rating", max=10)
        self.assertEqual(self.r.parse_text("8/10", ten), 8)

    def test_format_sort_and_file_check(self):
        self.assertEqual(self.r.format_text(4, self.stars), "4/5")
        self.assertEqual(sorted([3, 1, 5], key=lambda v: self.r.sort_key(v, self.stars)), [1, 3, 5])
        self.r.check_value(5, self.stars, "here")
        for value in (0, 6, 4.0, True, "4"):
            with self.assertRaises(BadFile) as caught:
                self.r.check_value(value, self.stars, "here")
            self.assertEqual(str(caught.exception), "here: the value of 'Confidence' must be a whole number from 1 to 5.")


class FindAndText(unittest.TestCase):
    def setUp(self):
        self.board = board(define({"name": "Story points", "type": "number"}, {"name": "Priority", "type": "choice", "choices": ["Low", "High"]},
                                  {"name": "Link", "type": "url"}),
                           {"story-points": 3, "priority": "high"}, {})

    def test_find_property(self):
        for given in ("story-points", "Story points", "STORY POINTS", "storypoints", "story-Points", " story  points "):
            self.assertEqual(P.find_property(self.board, given)["id"], "story-points", given)
        self.assertEqual(P.find_property(self.board, "priority")["name"], "Priority")
        for given in ("pionts", "", " - ", None, 3, ["priority"]):
            self.assertIsNone(P.find_property(self.board, given))
        self.assertIsNone(P.find_property({"name": "No properties"}, "x"))

    def test_an_exact_id_wins_over_a_name(self):
        renamed = board(P.clean_definitions(self.board, [{"id": "story-points", "name": "Effort", "type": "number"},
                                                         {"name": "Story points", "type": "text"}]))
        self.assertEqual(P.find_property(renamed, "story-points")["name"], "Effort")
        self.assertEqual(P.find_property(renamed, "Story points")["id"], "story-points-2")
        self.assertEqual(P.find_property(renamed, "effort")["id"], "story-points")

    def test_property_text(self):
        first, second = self.board["cards"]
        prop = P.find_property(self.board, "priority")
        self.assertEqual(P.property_text(self.board, first, prop), "High")
        self.assertEqual(P.property_text(self.board, first, "Story points"), "3")
        self.assertEqual(P.property_text(self.board, second, prop), "")
        self.assertEqual(P.property_text(self.board, first, "link"), "")
        self.assertEqual(P.property_text(self.board, first, "nothing"), "")
        self.assertEqual(P.property_text(self.board, {"key": "T-9"}, prop), "")


class CleanDefinitions(unittest.TestCase):
    def test_new_ids_are_slugs_unique_on_the_board_and_not_reserved(self):
        props = define({"name": "Points", "type": "number"}, {"name": "Points!", "type": "number"},
                       {"name": "Labels", "type": "text"}, {"name": "Summary", "type": "text"}, {"name": "日本", "type": "text"},
                       {"name": "x" * 40, "type": "text"})
        self.assertEqual([p["id"] for p in props], ["points", "points-2", "labels-2", "summary-2", "item", "x" * 40])
        for p in props:
            self.assertRegex(p["id"], schema.ID_RE)
            self.assertEqual(next(iter(p)), "id")
        self.assertTrue({"group", "labels", "field", "progress", "blocked"} <= P.RESERVED_IDS)

    def test_renames_keep_ids_and_new_entries_never_take_a_removed_id(self):
        b = board(define({"name": "Points", "type": "number"}, {"name": "Size", "type": "text"}))
        out = P.clean_definitions(b, [{"id": "size", "name": "Effort", "type": "text"}, {"name": "Points", "type": "number"},
                                      {"name": "Size", "type": "text"}])
        self.assertEqual([(p["id"], p["name"]) for p in out], [("size", "Effort"), ("points", "Points"), ("size-2", "Size")])
        gone = P.clean_definitions(b, [{"name": "Points!", "type": "number"}])
        self.assertEqual(gone[0]["id"], "points-2")
        made_up = P.clean_definitions(b, [{"id": "zzz", "name": "Pts", "type": "number"}])
        self.assertEqual(made_up[0]["id"], "pts")  # only ids already on the board are kept, like columns
        twice = P.clean_definitions(b, [{"id": "points", "name": "A", "type": "number"}, {"id": "points", "name": "B", "type": "number"}])
        self.assertEqual([p["id"] for p in twice], ["points", "b"])

    def test_keep_ids_for_imports(self):
        empty = {"properties": [], "cards": []}
        raw = [{"id": "est", "name": "Estimate", "type": "number"}, {"id": "Bad Id", "name": "Other", "type": "text"},
               {"id": "labels", "name": "Tags", "type": "text"}]
        self.assertEqual([p["id"] for p in P.clean_definitions(empty, raw, keep_ids=True)], ["est", "other", "tags"])
        self.assertEqual([p["id"] for p in P.clean_definitions(empty, raw)], ["estimate", "other", "tags"])

    def test_names_unique_ignoring_case_spaces_and_hyphens(self):
        for second in ("points", "POINTS", "Po ints", "po-ints"):
            with self.assertRaises(StoreError) as caught:
                define({"name": "Points", "type": "number"}, {"name": second, "type": "text"})
            self.assertEqual(str(caught.exception), f"There are two properties named {second}.")

    def test_list_level_errors(self):
        for raw, message in (("Points", "Properties must be a list."),
                             ([{"name": f"P{i}", "type": "text"} for i in range(31)], "A board can have at most 30 properties."),
                             (["Points"], "Each property needs a name and a type."),
                             ([{"name": "Points"}], "Points needs a type: text, number, choice, multi, checkbox, url, rating."),
                             ([{"name": "Points", "type": "date"}],
                              'There\'s no property type called "date". Types: text, number, choice, multi, checkbox, url, rating.'),
                             ([{"name": "Points", "type": ["number"]}],
                              "There's no property type called \"['number']\". Types: text, number, choice, multi, checkbox, url, rating.")):
            with self.assertRaises(StoreError) as caught:
                P.clean_definitions({"properties": [], "cards": []}, raw)
            self.assertEqual(str(caught.exception), message)
        self.assertEqual(len(define(*[{"name": f"P{i}", "type": "text"} for i in range(30)])), 30)
        self.assertEqual(P.clean_definitions({"name": "Version 2 board"}, []), [])

    def test_a_name_left_out_keeps_the_old_name(self):
        b = board(define({"name": "Points", "type": "number", "max": 5}))
        self.assertEqual(P.clean_definitions(b, [{"id": "points", "max": 8}])[0], {**b["properties"][0], "max": 8})

    def test_does_not_change_the_board(self):
        b = board(define({"name": "Points", "type": "number"}), {"points": 3})
        before = copy.deepcopy(b)
        P.clean_definitions(b, [{"id": "points", "name": "Effort", "type": "number"}])
        self.assertEqual(b, before)


class TypeChanges(unittest.TestCase):
    def change(self, b, **raw):
        return P.clean_definitions(b, [{"id": b["properties"][0]["id"], "name": b["properties"][0]["name"], **raw}])

    def test_any_change_when_no_card_uses_it(self):
        b = board(define({"name": "Points", "type": "number"}), {}, {"other": 1})
        for kind in P.TYPE_NAMES:
            options = {"choices": ["A"]} if kind in ("choice", "multi") else {}
            self.assertEqual(self.change(b, type=kind, **options)[0]["type"], kind)

    def test_refused_when_values_would_not_fit(self):
        b = board(define({"name": "Points", "type": "number"}), {"points": 3}, {"points": 5}, {})
        with self.assertRaises(StoreError) as caught:
            self.change(b, type="choice", choices=["3", "5"])
        self.assertEqual(str(caught.exception), "Points can't change from number to choice because 2 cards have values that "
                                                "wouldn't fit. Clear them first, or add a new property instead.")
        with self.assertRaises(StoreError) as caught:
            self.change(b, type="text")
        self.assertIn("2 cards have values", str(caught.exception))
        one_card = board(define({"name": "Link", "type": "url"}), {"link": "https://example.com"})
        with self.assertRaises(StoreError) as caught:
            self.change(one_card, type="checkbox")
        self.assertEqual(str(caught.exception), "Link can't change from url to checkbox because 1 card has a value that "
                                                "wouldn't fit. Clear it first, or add a new property instead.")

    def test_choice_ids_never_turn_into_text(self):
        b = board(define({"name": "Priority", "type": "choice", "choices": ["High"]}), {"priority": "high"})
        with self.assertRaises(StoreError):
            self.change(b, type="text")

    def test_allowed_when_every_value_fits(self):
        links = board(define({"name": "Link", "type": "text"}), {"link": "https://example.com"}, {"link": "http://a.test"})
        self.assertEqual(self.change(links, type="url")[0]["type"], "url")
        words = board(define({"name": "Link", "type": "url"}), {"link": "https://example.com"})
        self.assertEqual(self.change(words, type="text")[0]["type"], "text")
        mixed = board(define({"name": "Link", "type": "text"}), {"link": "https://example.com"}, {"link": "see the wiki"})
        with self.assertRaises(StoreError):
            self.change(mixed, type="url")
        numbers = board(define({"name": "Score", "type": "number"}), {"score": 4}, {"score": 1})
        self.assertEqual(self.change(numbers, type="rating")[0]["max"], 5)
        with self.assertRaises(StoreError):
            self.change(numbers, type="rating", max=3)
        with self.assertRaises(StoreError):
            self.change(board(define({"name": "Score", "type": "number", "decimals": 1}), {"score": 2.5}), type="rating")
        stars = board(define({"name": "Score", "type": "rating"}), {"score": 4})
        self.assertEqual(self.change(stars, type="number")[0]["type"], "number")
        with self.assertRaises(StoreError):
            self.change(stars, type="number", max=3)

    def test_choice_and_multi_convert(self):
        b = board(define({"name": "Platform", "type": "choice", "choices": ["iOS", "Web"]}), {"platform": "web"}, {})
        b["properties"] = self.change(b, type="multi")
        self.assertEqual([c["id"] for c in b["properties"][0]["choices"]], ["ios", "web"])
        self.assertEqual(P.prune_values(b), 0)
        self.assertEqual(b["cards"][0]["properties"], {"platform": ["web"]})
        b["properties"] = self.change(b, type="choice")
        self.assertEqual(P.prune_values(b), 0)
        self.assertEqual(b["cards"][0]["properties"], {"platform": "web"})
        two = board(define({"name": "Platform", "type": "multi", "choices": ["iOS", "Web"]}), {"platform": ["ios", "web"]})
        with self.assertRaises(StoreError) as caught:
            self.change(two, type="choice")
        self.assertIn("1 card has a value that wouldn't fit", str(caught.exception))
        renamed = self.change(board(define({"name": "P", "type": "choice", "choices": ["A"]}), {"p": "a"}),
                              type="multi", choices=[{"name": "a"}, "B"])
        self.assertEqual([c["id"] for c in renamed[0]["choices"]], ["a", "b"])

    def test_a_name_matched_entry_is_protected_too(self):
        b = board(define({"name": "Points", "type": "number"}), {"points": 3})
        with self.assertRaises(StoreError):
            P.clean_definitions(b, [{"name": "points", "type": "checkbox"}])


class CheckDefinitions(unittest.TestCase):
    def good(self):
        return {"name": "Test", "properties": [
            {"id": "points", "name": "Points", "type": "number", "onCard": True, "min": 0, "max": None, "decimals": 1, "unit": "h",
             "future": "kept"},
            {"id": "priority", "name": "Priority", "type": "choice", "choices": [{"id": "low", "name": "Low"}, {"id": "high", "name": "High", "color": "rose"}]},
            {"id": "platform", "name": "Platform", "type": "multi", "choices": [{"id": "ios", "name": "iOS", "color": "blue"}]},
            {"id": "reviewed", "name": "Reviewed", "type": "checkbox"},
            {"id": "link", "name": "Link", "type": "url"},
            {"id": "stars", "name": "Stars", "type": "rating"},
            {"id": "note", "name": "Note", "type": "text"}],
            "cards": [{"key": "T-1", "properties": {"points": 1.5, "priority": "high", "platform": ["ios"], "reviewed": True,
                                                    "link": "https://example.com", "stars": 4, "note": "Hi"}},
                      {"key": "T-2"}]}

    def test_a_good_board_passes_and_gets_its_defaults(self):
        b = self.good()
        P.check_definitions(b, "f")
        props = {p["id"]: p for p in b["properties"]}
        self.assertEqual(props["points"]["future"], "kept")
        self.assertEqual(props["priority"]["choices"][0]["color"], "slate")
        self.assertEqual(props["priority"]["choices"][1]["color"], "rose")
        self.assertIs(props["reviewed"]["onCard"], False)
        self.assertEqual(props["stars"]["max"], 5)
        self.assertEqual(b["cards"][1]["properties"], {})
        bare = {"name": "Old", "cards": [{"key": "T-1"}]}
        P.check_definitions(bare, "f")
        self.assertEqual((bare["properties"], bare["cards"][0]["properties"]), ([], {}))
        template = {"name": "A template", "properties": []}
        P.check_definitions(template, "f")
        number = {"properties": [{"id": "n", "name": "N", "type": "number"}]}
        P.check_definitions(number, "f")
        self.assertEqual(number["properties"][0], {"id": "n", "name": "N", "type": "number", "onCard": False, "min": None,
                                                   "max": None, "decimals": 0, "unit": ""})

    def test_clean_definitions_pass_the_check(self):
        b = board(define({"name": "Points", "type": "number", "min": 0.5, "decimals": 1}, {"name": "Priority", "type": "choice", "choices": ["Low"]},
                         {"name": "Platform", "type": "multi", "choices": ["iOS", "Web"]}, {"name": "Stars", "type": "rating", "max": 10},
                         {"name": "Labels", "type": "checkbox"}, {"name": "Link", "type": "url"}, {"name": "Note", "type": "text"}),
                  {"points": 2.5, "priority": "low", "platform": ["ios", "web"], "stars": 10, "labels-2": True})
        before = copy.deepcopy(b)
        P.check_definitions(stored(b), "f")
        self.assertEqual(b, before)

    def test_bad_definitions(self):
        cases = [
            (lambda b: b.update(properties={}), "f: properties must be a list."),
            (lambda b: b["properties"].append("Points"), "f: each property needs an id, a name and a type."),
            (lambda b: b["properties"].append({"id": "d", "name": "Due", "type": "date"}),
             "f: property 'Due' has no known type (the types are text, number, choice, multi, checkbox, url, rating)."),
            (lambda b: b["properties"].append({"name": "X", "type": "text"}), "f: each property needs an id and a name."),
            (lambda b: b["properties"].append({"id": "Big", "name": "X", "type": "text"}),
             "f, property 'X': its id must be lowercase letters, digits and hyphens."),
            (lambda b: b["properties"].append({"id": "labels", "name": "X", "type": "text"}),
             "f, property 'X': its id 'labels' already means something else on a board."),
            (lambda b: b["properties"].append({"id": "note", "name": "X", "type": "text"}), "f: two properties share the id 'note'."),
            (lambda b: b["properties"][0].update(onCard=1), "f, property 'Points': onCard must be true or false."),
            (lambda b: b["properties"][0].update(decimals=7), "f, property 'Points': decimals must be a whole number from 0 to 4."),
            (lambda b: b["properties"][0].update(min="0"), "f, property 'Points': min must be a number or null."),
            (lambda b: b["properties"][0].update(min=5, max=1), "f, property 'Points': min can't be more than max."),
            (lambda b: b["properties"][0].update(unit=None), "f, property 'Points': unit must be text."),
            (lambda b: b["properties"][1].update(choices=[]), "f, property 'Priority': it needs a list with at least one choice."),
            (lambda b: b["properties"][1]["choices"].append({"id": "low", "name": "Again"}), "f, property 'Priority': two choices share the id 'low'."),
            (lambda b: b["properties"][1]["choices"].append({"name": "No id"}), "f, property 'Priority': each choice needs an id and a name."),
            (lambda b: b["properties"][1]["choices"][0].update(color="pink"), "f, property 'Priority': choice 'Low' has an unknown color."),
            (lambda b: b["properties"][5].update(max=20), "f, property 'Stars': max must be a whole number from 3 to 10."),
        ]
        for spoil, message in cases:
            b = self.good()
            spoil(b)
            with self.assertRaises(BadFile) as caught:
                P.check_definitions(b, "f")
            self.assertEqual(str(caught.exception), message)
        with self.assertRaises(BadFile):
            T["text"].check_definition({"id": "note", "name": "Note", "type": "url"}, "f")

    def test_bad_card_values(self):
        cases = [
            ({"properties": []}, "f, card T-1: properties must map property ids to values."),
            ({"properties": {"gone": 1}}, "f, card T-1: it has a value for a property that doesn't exist ('gone')."),
            ({"properties": {"points": None}}, "f, card T-1: the value of 'Points' must be a number."),
            ({"properties": {"points": 1.25}}, "f, card T-1: the value of 'Points' has more than 1 decimal place."),
            ({"properties": {"priority": "Low"}}, "f, card T-1: the value of 'Priority' must be the id of one of its choices."),
            ({"properties": {"platform": ["ios", "ios"]}}, "f, card T-1: the value of 'Platform' must list each choice once, in the order of the choices."),
            ({"properties": {"reviewed": False}}, "f, card T-1: the value of 'Reviewed' must be true (a box that isn't ticked is left out)."),
            ({"properties": {"stars": 6}}, "f, card T-1: the value of 'Stars' must be a whole number from 1 to 5."),
            ({"properties": {"link": "javascript:alert(1)"}},
             "f, card T-1: the value of 'Link' must be a link starting with http:// or https://, with no spaces, at most 500 characters."),
        ]
        for card, message in cases:
            b = self.good()
            b["cards"][0].update(card)
            with self.assertRaises(BadFile) as caught:
                P.check_definitions(b, "f")
            self.assertEqual(str(caught.exception), message)
        b = self.good()
        b["cards"].append("junk")
        with self.assertRaises(BadFile):
            P.check_definitions(b, "f")


class PruneValues(unittest.TestCase):
    def setUp(self):
        self.props = define({"name": "Points", "type": "number", "decimals": 2, "max": 100},
                            {"name": "Priority", "type": "choice", "choices": ["Low", "High"]},
                            {"name": "Platform", "type": "multi", "choices": ["iOS", "Android", "Web"]},
                            {"name": "Stars", "type": "rating", "max": 10}, {"name": "Note", "type": "text"})
        self.board = board(self.props,
                           {"points": 2.25, "priority": "high", "platform": ["ios", "web"], "stars": 9, "note": "a"},
                           {"points": 50, "priority": "low", "platform": ["android"], "stars": 2},
                           {})

    def redefine(self, raw):
        self.board["properties"] = P.clean_definitions(self.board, raw)
        return P.prune_values(self.board)

    def values(self):
        return [c["properties"] for c in self.board["cards"]]

    def keep(self, **changes):
        """Every current definition, with some changed, as the page sends them."""
        return [{**p, **changes.get(p["id"], {})} for p in self.board["properties"] if changes.get(p["id"]) != "remove"]

    def test_nothing_to_do(self):
        cards = self.board["cards"]
        self.assertEqual(P.prune_values(self.board), 0)
        self.assertIs(self.board["cards"], cards)
        self.assertEqual(self.redefine(self.keep(points={"name": "Effort"})), 0)
        self.assertIs(self.board["cards"], cards)
        self.assertEqual(P.prune_values({"name": "Version 2 board"}), 0)

    def test_removed_property(self):
        self.assertEqual(self.redefine(self.keep(note="remove", stars="remove")), 3)
        self.assertEqual(self.values()[0], {"points": 2.25, "priority": "high", "platform": ["ios", "web"]})
        self.assertEqual(self.values()[1], {"points": 50, "priority": "low", "platform": ["android"]})

    def test_removed_choices(self):
        choices = self.board["properties"]
        priority = {"choices": [c for c in choices[1]["choices"] if c["id"] != "high"]}
        platform = {"choices": [c for c in choices[2]["choices"] if c["id"] != "web"]}
        self.assertEqual(self.redefine(self.keep(priority=priority, platform=platform)), 2)
        self.assertEqual(self.values()[0]["platform"], ["ios"])
        self.assertNotIn("priority", self.values()[0])
        self.assertEqual(self.values()[1], {"points": 50, "priority": "low", "platform": ["android"], "stars": 2})
        only = {"choices": [c for c in self.board["properties"][2]["choices"] if c["id"] == "ios"]}
        self.assertEqual(self.redefine(self.keep(platform=only)), 1)
        self.assertNotIn("platform", self.values()[1])

    def test_reordered_choices_reorder_values_without_counting(self):
        platform = {"choices": list(reversed(self.board["properties"][2]["choices"]))}
        self.assertEqual(self.redefine(self.keep(platform=platform)), 0)
        self.assertEqual(self.values()[0]["platform"], ["web", "ios"])

    def test_tightened_limits(self):
        self.assertEqual(self.redefine(self.keep(points={"max": 10}, stars={"max": 5})), 2)
        self.assertEqual(self.values()[0], {"points": 2.25, "priority": "high", "platform": ["ios", "web"], "note": "a"})
        self.assertEqual(self.values()[1], {"priority": "low", "platform": ["android"], "stars": 2})
        self.assertEqual(self.redefine(self.keep(points={"decimals": 1})), 1)
        self.assertEqual(self.values()[0]["points"], 2.3)
        self.assertEqual(self.redefine(self.keep(points={"decimals": 0, "min": 3})), 1)
        self.assertNotIn("points", self.values()[0])

    def test_never_edits_a_card_in_place(self):
        cards = self.board["cards"]
        originals = copy.deepcopy(cards)
        self.redefine(self.keep(note="remove", points={"decimals": 0}))
        self.assertIsNot(self.board["cards"], cards)
        self.assertEqual(cards, originals)
        self.assertIs(self.board["cards"][2], cards[2])  # untouched cards are shared, not copied
        P.check_definitions(stored(self.board), "after pruning")

    def test_junk_values_are_dropped(self):
        self.board["cards"][2]["properties"] = {"points": "x", "priority": ["high"], "platform": "web", "stars": 0}
        self.assertEqual(P.prune_values(self.board), 2)
        self.assertEqual(self.values()[2], {"priority": "high", "platform": ["web"]})


if __name__ == "__main__":
    unittest.main()
