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


# ── check_weight_consistency ────────────────────────────────────────────────

def test_weight_recipe_flags_large_gap():
    r = {"size_type": "weight", "ref_weight_kg": 0.35}
    assert db.check_weight_consistency(r, [], 451)          # +29%
    assert not db.check_weight_consistency(r, [], 370)      # +5.7%


def test_weight_recipe_flags_grams_in_kg_field():
    r = {"size_type": "weight", "ref_weight_kg": 1500}
    assert not db.check_weight_consistency(r, [], 1480)
    msgs = db.check_weight_consistency(r, [], 1188)
    assert msgs and "grams entered in the kg field" in msgs[0]


def test_diameter_variant_scaled_to_its_size():
    r = {"size_type": "diameter", "ref_diameter_cm": 22, "ref_height_cm": 5}
    # est 1440g at 22cm; a 14cm cake is (14/22)^2 = 0.405x -> ~583g
    ok  = {"format": "standard", "ref_weight_g": 580, "ref_diameter_cm": 14}
    bad = {"format": "standard", "ref_weight_g": 400, "ref_diameter_cm": 14}
    assert not db.check_weight_consistency(r, [ok], 1440)
    assert db.check_weight_consistency(r, [bad], 1440)


def test_variants_without_diameter_or_non_standard_are_skipped():
    r = {"size_type": "diameter", "ref_diameter_cm": 22}
    vs = [{"format": "standard", "ref_weight_g": 100},
          {"format": "individual", "ref_weight_g": 100, "ref_diameter_cm": 22}]
    assert db.check_weight_consistency(r, vs, 1440) == []
    assert db.check_weight_consistency(r, vs, 0) == []
