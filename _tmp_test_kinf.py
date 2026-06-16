"""Tests for IndependentOperator.estimate_k_inf (openmc-dev/openmc #2132)."""
import numpy as np
import pytest
from pathlib import Path

import openmc
from openmc import Material
from openmc.deplete import IndependentOperator, MicroXS, Chain

CHAIN_PATH = Path(__file__).parents[1] / "chain_simple.xml"
ONE_GROUP_XS = Path(__file__).parents[1] / "micro_xs_simple.csv"


def _build_uo2_operator():
    """Build a UO2-fuel IndependentOperator with a thermal flux and 1G xs."""
    volume = 1.0
    nuclides = {
        'U234': 8.922411359424315e+18,
        'U235': 9.98240191860822e+20,
        'U238': 2.2192386373095893e+22,
        'U236': 4.5724195495061115e+18,
        'O16':  4.639065406771322e+22,
        'O17':  1.7588724018066158e+19,
    }
    flux = 1.0
    micro_xs = MicroXS.from_csv(ONE_GROUP_XS)
    chain = Chain.from_xml(CHAIN_PATH)
    return IndependentOperator.from_nuclides(
        volume, nuclides, flux, micro_xs, chain, nuc_units='atom/cm3')


def test_estimate_k_inf_smoke():
    """Sanity: k_inf can be computed for a UO2 operator and is > 0."""
    op = _build_uo2_operator()
    k = op.estimate_k_inf()
    assert np.isfinite(k)
    assert k > 0.0
    # A well-moderated UO2 LWR should have k_inf > 1 (critical config
    # is 4-5% enriched, so thermal k_inf is typically 1.2-1.5).
    # We just check the estimate is in a physically reasonable band.
    assert 0.1 < k < 5.0


def test_estimate_k_inf_user_override():
    """User-supplied nu_fission overrides the built-in defaults."""
    op = _build_uo2_operator()
    k_default = op.estimate_k_inf()
    k_boosted = op.estimate_k_inf(nu_fission={'U235': 5.0})
    # A higher nu_bar for U235 must increase k_inf (since U235 fissions
    # dominate the numerator) and the change should be > 0.
    assert k_boosted > k_default


def test_estimate_k_inf_unknown_nuclide_raises():
    """If a fissionable nuclide is missing from defaults, raise clearly."""
    op = _build_uo2_operator()
    # Build a tiny synthetic MicroXS containing a fictitious nuclide.
    # We can't easily swap micro_xs post-init, so instead we check that
    # a user call with the defaults dictionary missing all real fissile
    # nuclides raises a ValueError.
    bad = {}  # remove all defaults
    with pytest.raises(ValueError, match="nu_bar for fission of"):
        op.estimate_k_inf(nu_fission=bad)


def test_estimate_k_inf_rejects_missing_volume():
    """A material with no volume set must raise a clear error."""
    nuclides = {'U235': 1.0, 'O16': 2.0}
    micro_xs = MicroXS.from_csv(ONE_GROUP_XS)
    chain = Chain.from_xml(CHAIN_PATH)
    # from_nuclides sets volume; use the lower-level constructor to
    # build a material with no volume.
    fuel = Material(name="uo2")
    fuel.add_nuclide('U235', 0.04)
    fuel.add_nuclide('O16',  0.96)
    fuel.set_density('g/cc', 10.0)
    fuel.depletable = True
    op = IndependentOperator(
        materials=[fuel],
        fluxes=[1.0],
        micros=[micro_xs],
        chain_file=CHAIN_PATH,
    )
    with pytest.raises(ValueError, match="has no volume set"):
        op.estimate_k_inf()
