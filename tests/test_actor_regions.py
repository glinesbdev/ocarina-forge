from ocarina_forge.actors import is_region, is_volume


def test_rolling_rock_land_is_geometry_and_real_regions_stay_outlines():
    road = "Land_Kokiri_RollingRockArea_A_08"
    assert not is_region(road)
    assert not is_volume(road)
    assert is_region(f"{road}_AirWall")
    assert is_region("Area_RollingRock")
    assert is_volume(road, [8, 1, 1])
