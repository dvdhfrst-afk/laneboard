"""The search and templates commands, and cards with --query, --sort and --view."""
import json

from tests.test_cli_board_parts import Case


class CardsAndSearch(Case):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "Work")
        self.run_cli("labels", "Work", "--add", "Bug")
        self.run_cli("properties", "Work", "--add", "Points", "--type", "number")
        for title, column in (("Login page crashes", "Backlog"), ("Write the guide", "Backlog"), ("Old login bug", "Done"),
                              ("Fix the login form", "Done")):
            self.run_cli("add", "Work", title, "--column", column)
        self.run_cli("edit", "Work", "T-1", "--label", "Bug", "--set", "Points=5")
        self.run_cli("edit", "Work", "T-2", "--set", "Points=2")
        self.run_cli("edit", "Work", "T-4", "--label", "Bug")
        self.run_cli("archive", "Work", "T-3")
        self.run_cli("new", "Home")
        self.run_cli("add", "Home", "Login to the bank")

    def keys(self, *args):
        return [line.split()[0] for line in self.lines(*args) if line != "No cards to show."]

    def test_cards_lists_every_card_as_before(self):
        self.assertEqual(self.lines("cards", "Work"), [
            "T-1          Backlog        Login page crashes  [Bug]", "T-2          Backlog        Write the guide",
            "T-4          Done           Fix the login form  [Bug]"])
        self.assertEqual(self.keys("cards", "Work", "--archived"), ["T-3"])
        self.assertEqual(self.keys("cards", "Work", "--column", "done"), ["T-4"])

    def test_cards_with_a_search_and_a_sort(self):
        self.assertEqual(self.keys("cards", "Work", "--query", "login"), ["T-1", "T-4"])
        self.assertEqual(self.keys("cards", "Work", "--query", "login", "--column", "Backlog"), ["T-1"])
        self.assertEqual(self.keys("cards", "Work", "--query", "login", "--archived"), ["T-3"])
        self.assertEqual(self.keys("cards", "Work", "--query", "is:archived"), ["T-3"])
        self.assertEqual(self.keys("cards", "Work", "--sort=-title"), ["T-2", "T-1", "T-4"])
        self.assertEqual(self.keys("cards", "Work", "--sort=points"), ["T-2", "T-1", "T-4"])
        self.assertEqual(self.keys("cards", "Work", "--query", "nothing like it"), [])
        cards = json.loads(self.run_cli("cards", "Work", "--query", "label:bug points:>3", "--json")[0])
        self.assertEqual([c["key"] for c in cards], ["T-1"])

    def test_cards_with_a_saved_view(self):
        self.run_cli("views", "Work", "--add", "Bugs", "--query", "label:bug", "--sort=-title")
        self.assertEqual(self.keys("cards", "Work", "--view", "bugs"), ["T-1", "T-4"])
        self.assertEqual(self.keys("cards", "Work", "--view", "Bugs", "--sort", "title", "--column", "Done"), ["T-4"])
        self.unchanged("cards", "Work", "--view", "Bugs", "--query", "x", code=2, says="not allowed with argument")
        self.unchanged("cards", "Work", "--view", "Nope", says="Work has no saved view called 'Nope'. It has: Bugs.")

    def test_a_view_that_stopped_working_says_so(self):
        self.run_cli("views", "Work", "--add", "Big", "--query", "label:bug points:>3")
        self.run_cli("properties", "Work", "--remove", "Points")
        err = self.unchanged("cards", "Work", "--view", "Big")
        self.assertEqual(err.splitlines(), ['laneboard: The saved view "Big" doesn\'t work any more: There\'s no qualifier called '
                                            '"points". Qualifiers: column, group, label, key, title, notes, is, has, tasks, progress, '
                                            'sort.', "  label:bug points:>3", "            ^"])

    def test_a_search_that_does_not_read_points_at_the_spot(self):
        err = self.unchanged("cards", "Work", "--query", "login pionts:3")
        self.assertEqual(err.splitlines(), ['laneboard: There\'s no property called "pionts". Properties: Points.',
                                            "  login pionts:3", "        ^"])
        err = self.unchanged("cards", "Work", "--sort=title,-size")
        self.assertEqual(err.splitlines()[1:], ["  title,-size", "         ^"])

    def test_search_every_board(self):
        self.assertEqual(self.lines("search", "login"), [
            "Work (2 of 3 cards)", "  T-1          Backlog        Login page crashes  [Bug]",
            "  T-4          Done           Fix the login form  [Bug]",
            "Home (1 of 1 card)", "  T-1          Backlog        Login to the bank"])
        self.assertEqual(self.lines("search", "login", "--board", "home"), ["Home (1 of 1 card)",
                                                                           "  T-1          Backlog        Login to the bank"])
        self.assertEqual(self.lines("search", "label:bug", "--sort=-key"), [
            "Work (2 of 3 cards)", "  T-1          Backlog        Login page crashes  [Bug]",
            "  T-4          Done           Fix the login form  [Bug]"])
        self.assertEqual(self.lines("search", "nothing like it"), ["No cards match."])
        found = json.loads(self.run_cli("search", "login is:archived", "--json")[0])
        self.assertEqual([(r["name"], r["total"], [c["key"] for c in r["cards"]]) for r in found], [("Work", 4, ["T-3"])])

    def test_search_errors(self):
        err = self.unchanged("search", "label:bgu")
        self.assertEqual(err.splitlines(), ['laneboard: There\'s no label called "bgu". Labels: Bug.', "  label:bgu", "        ^"])
        err = self.unchanged("search", "login", "--board", "Home", "--sort", "points")
        self.assertIn('There\'s nothing called "points" to sort by.', err)
        self.unchanged("search", "x", "--board", "Nope", says="There's no board called 'Nope'.")


class Templates(Case):
    def test_the_listing(self):
        lines = self.lines("templates")
        self.assertEqual(len(lines), 6)
        self.assertTrue(lines[0].startswith("basic                Basic (5 columns, 4 starter cards). Five plain columns"))
        listed = json.loads(self.run_cli("templates", "--json")[0])
        self.assertEqual([t["id"] for t in listed], ["basic", "software", "personal", "content", "support", "research"])
        self.run_cli("templates", "--json", "delete", "basic", "--yes", code=2)

    def test_a_new_board_from_a_template(self):
        out = self.lines("new", "Chores", "--template", "personal")
        self.assertRegex(out[0], r"^Added Chores as b-[0-9a-f]{8}, from the template Personal, with 4 cards\.$")
        self.assertEqual([c["name"] for c in self.board("Chores")["columns"]], ["To do", "Doing", "Done"])
        self.run_cli("new", "", "--template", "Software team")
        self.assertEqual(len(self.board("Software team")["cards"]), 5)
        self.unchanged("new", "X", "--template", "weekly", says="There's no template called weekly. The templates are: Basic")

    def test_saving_and_deleting(self):
        self.run_cli("new", "Work", "--template", "software")
        self.assertEqual(self.lines("templates", "save", "Work", "Our setup", "--description", "How we work"),
                         ["Saved Our setup as the template our-setup (5 columns, no starter cards)."])
        self.assertEqual(self.lines("templates", "save", "Work", "With cards", "--cards"),
                         ["Saved With cards as the template with-cards (5 columns, 5 starter cards)."])
        self.assertEqual(self.lines("templates")[-2:], [
            "our-setup            Our setup (5 columns, no starter cards, saved). How we work",
            "with-cards           With cards (5 columns, 5 starter cards, saved)."])
        self.unchanged("templates", "save", "Work", "our SETUP", says="There's already a template called Our setup.")
        self.unchanged("templates", "delete", "Our setup", code=2, says="add --yes to delete it")
        self.unchanged("templates", "delete", "Basic", "--yes", says="Basic is a built-in template")
        self.assertEqual(self.lines("templates", "delete", "our setup", "--yes"), ["Deleted the template Our setup."])
        self.run_cli("new", "Again", "--template", "With cards")
        self.assertEqual(len(self.board("Again")["cards"]), 5)
        self.assertEqual(self.board("Work")["rev"], 1, "saving templates never changes a board")


if __name__ == "__main__":
    import unittest
    unittest.main()
