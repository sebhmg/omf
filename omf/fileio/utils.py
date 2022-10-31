from numpy import testing


def compare_elements(elem_a, elem_b):
    """Cycle through attributes and check equal."""

    assert elem_a.name == elem_b.name

    if hasattr(elem_a, "geometry"):
        for attr in elem_a.geometry._valid_locations:
            testing.assert_allclose(
                getattr(elem_a.geometry, attr).array,
                getattr(elem_b.geometry, attr).array,
            )

    if hasattr(elem_a, "array"):
        testing.assert_allclose(elem_a.array.array, elem_b.array.array)

    if hasattr(elem_a, "data") and elem_a.data:
        for data_a, data_b in zip(elem_a.data, elem_b.data):
            compare_elements(data_a, data_b)

    if hasattr(elem_a, "colormap") and elem_a.colormap:
        compare_elements(elem_a.colormap, elem_b.colormap)
