# -*- coding: utf-8 -*-
"""Tests for the curated variable-name map (no QGIS/geobridge needed)."""

from variable_labels import VARIABLE_LABELS, friendly_name, has_label


def test_known_short_code_maps_to_readable_name():
    assert friendly_name("t2m") == "2-metre temperature"
    assert friendly_name("utci") == "Universal Thermal Climate Index (UTCI)"
    assert has_label("t2m") is True


def test_unknown_code_falls_back_to_prettified():
    assert friendly_name("some_new_var") == "some new var"
    assert has_label("some_new_var") is False


def test_empty_is_safe():
    assert friendly_name("") == ""
    assert has_label("") is False


def test_all_labels_are_nonempty_strings():
    for code, label in VARIABLE_LABELS.items():
        assert isinstance(label, str) and label.strip()
        assert code == code.strip()
