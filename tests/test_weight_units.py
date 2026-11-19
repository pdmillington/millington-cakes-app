"""Recipe weight / label grams must follow the ingredient's pack_unit."""
import millington_db as db


def _line(name, amount, unit):
    return {"ingredient_name": name, "amount": amount, "ingredient_unit": unit}


def test_orange_priced_per_kg_is_grams_not_count():
    r = db.estimate_recipe_weight([_line("Naranja", 80, "g")])
    assert r["weight_g"] == 80
    r = db.estimate_recipe_weight([_line("Naranja", 40, "kg")])
    assert r["weight_g"] == 40            # small amounts too: not 40 x 80g


def test_eggs_counted_in_units():
    r = db.estimate_recipe_weight([_line("Huevos", 4, "units")])
    assert r["weight_g"] == 200


def test_unit_ingredient_without_known_weight_is_flagged():
    r = db.estimate_recipe_weight([_line("Pasta Brick", 3, "units")])
    assert r["weight_g"] == 0 and r["excluded"] == ["Pasta Brick (3)"]


def test_name_collision_does_not_trigger_count():
    # "limas" is a substring of "Limasol"; the litre pack_unit settles it
    r = db.estimate_recipe_weight([_line("Zumo Lima Limasol", 30, "l")])
    assert r["weight_g"] == 30


def test_small_measured_amounts_are_added():
    r = db.estimate_recipe_weight([_line("Sal", 5, "g")])
    assert r["weight_g"] == 5 and not r["excluded"]


def test_unknown_unit_falls_back_to_name_heuristic():
    assert db.estimate_recipe_weight([_line("Manzanas", 3, None)])["weight_g"] == 450
    assert db.estimate_recipe_weight([_line("Manzanas", 300, None)])["weight_g"] == 300


def test_label_grams():
    assert db._to_label_grams("Naranja", 40, "g") == 40
    assert db._to_label_grams("Huevos", 4, "units") == 200
    assert db._to_label_grams("Vainilla rama", 1, None) == 0
