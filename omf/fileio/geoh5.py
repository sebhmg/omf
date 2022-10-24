from __future__ import annotations

import warnings
from abc import ABC
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
from geoh5py.data import Data, FloatData
from geoh5py.groups import RootGroup
from geoh5py.objects import BlockModel, Curve, Grid2D, Points, Surface
from geoh5py.shared import Entity
from geoh5py.workspace import Workspace

from omf.base import Project, UidModel
from omf.data import Int2Array, ScalarArray, ScalarData, Vector3Array
from omf.lineset import LineSetElement, LineSetGeometry
from omf.pointset import PointSetElement, PointSetGeometry
from omf.surface import SurfaceElement, SurfaceGeometry, SurfaceGridGeometry
from omf.volume import VolumeElement, VolumeGridGeometry


class OMFtoGeoh5NotImplemented(NotImplementedError):
    """Custom error message for attributes not implemented by geoh5."""

    def __init__(
        self,
        name: str,
    ):
        super().__init__(OMFtoGeoh5NotImplemented.message(name))

    @staticmethod
    def message(info):
        """Custom error message."""
        return f"Cannot perform the conversion from OMF to geoh5. {info}"


class GeoH5Writer:  # pylint: disable=too-few-public-methods
    """
    OMF to geoh5 class converter
    """

    def __init__(self, element: UidModel, file_name: str | Path):

        if not isinstance(file_name, (str, Path)):
            raise TypeError("Input 'file' must be of str or Path.")

        self.file = file_name
        self.entity = element

    @property
    def entity(self):
        return self._entity

    @entity.setter
    def entity(self, element):
        if type(element) not in _CONVERSION_MAP:
            raise OMFtoGeoh5NotImplemented(
                f"Element of type {type(element)} currently not implemented."
            )

        converter = _CONVERSION_MAP[type(element)](element, self.file)
        self._entity = converter.from_omf()


class GeoH5Reader:  # pylint: disable=too-few-public-methods
    """
    Geoh5 to omf class converter
    """

    def __init__(self, file_name: str | Path):

        with Workspace(file_name, mode="r") as workspace:
            self.file = workspace
            converter = ProjectConversion(workspace.root, self.file)
            self.project = converter.from_geoh5()


class BaseConversion(ABC):
    """
    Base conversion between OMF and geoh5 format.
    """

    geoh5: str | Path | Workspace = None
    geoh5_type = Entity
    omf_type: type[UidModel] = UidModel
    _attribute_map: dict = {
        "uid": "uid",
        "name": "name",
    }
    _element = None
    _entity = None

    def __init__(self, obj: UidModel | Entity, geoh5: str | Path | Workspace):
        if isinstance(obj, self.omf_type):
            self.element = obj
        elif isinstance(obj, self.geoh5_type):
            self.entity = obj
        else:
            raise TypeError(
                f"Input object should be an instance of {self.omf_type} or {self.geoh5_type}"
            )

        self.geoh5 = geoh5

    def collect_omf_attributes(self, **kwargs):
        with fetch_h5_handle(self.geoh5) as workspace:
            for key, alias in self._attribute_map.items():
                prop = getattr(self.element, key, None)

                if type(prop) in _CONVERSION_MAP:
                    converter = _CONVERSION_MAP[type(prop)](prop, workspace)

                    if converter.geoh5_type is np.ndarray:
                        kwargs[alias] = converter.from_omf()
                    else:
                        kwargs = converter.from_omf(**kwargs)

                else:
                    kwargs[alias] = prop

        return kwargs

    def collect_h5_attributes(self, **kwargs):
        with fetch_h5_handle(self.geoh5) as workspace:
            for key, alias in self._attribute_map.items():

                if isinstance(alias, type(BaseConversion)):
                    prop = alias(  # pylint: disable=not-callable
                        self.entity, workspace
                    ).from_geoh5()
                else:
                    prop = getattr(self.entity, alias, None)

                if prop is not None:
                    kwargs[key] = prop

        return kwargs

    @property
    def element(self):
        if self._element is None and self._entity is not None:
            self.from_geoh5()
        return self._element

    @element.setter
    def element(self, value: UidModel):
        if not isinstance(value, self.omf_type):
            raise ValueError(f"Input 'element' must be of type {self.omf_type}")
        self._element = value

    @property
    def entity(self):
        if self._entity is None and self._element is not None:
            self.from_omf()
        return self._entity

    @entity.setter
    def entity(self, value: Entity):
        if not isinstance(value, self.geoh5_type):
            raise ValueError(f"Input 'entity' must be of type {self.omf_type}")
        self._entity = value

    def from_omf(self, **kwargs) -> Entity | None:
        """Convert omf element to geoh5 entity."""

    def from_geoh5(self) -> UidModel:
        """Convert geoh5 entity to omf element."""

    def process_dependents(self, parent: UidModel | Entity, workspace) -> list:
        children = []
        if isinstance(parent, UidModel):

            if getattr(parent, "data", None):
                for child in parent.data:
                    converter = _CONVERSION_MAP[type(child)](child, workspace)
                    children += [converter.from_omf(parent=self.entity)]
            elif getattr(parent, "elements", None):
                for child in parent.elements:
                    converter = _CONVERSION_MAP[type(child)](child, workspace)
                    children += [converter.from_omf(parent=self.entity)]

        elif isinstance(parent, Entity) and getattr(parent, "children", None):
            for child in parent.children:
                converter = _CONVERSION_MAP[type(child)](child, workspace)
                children += [converter.from_geoh5()]

        return children


class DataConversion(BaseConversion):
    """
    Conversion between :obj:`omf.data.ScalarData` and
    :obj:`geoh5py.data.Data`
    """

    omf_type = ScalarData
    geoh5_type = Data
    _attribute_map: dict[str, Any] = {
        "uid": "uid",
        "name": "name",
        "array": "values",
        "colormap": "color_map",
    }

    def from_omf(self, parent=None, **kwargs) -> Data:
        with fetch_h5_handle(self.geoh5):
            kwargs = self.collect_omf_attributes(**kwargs)

            if self.element.location in ["faces", "cells", "segments"]:
                kwargs["association"] = "CELL"
            else:
                kwargs["association"] = "VERTEX"

            self._entity = parent.add_data({self.element.name: kwargs})

        return self._entity

    def from_geoh5(self, **kwargs) -> UidModel:
        """Convert a geoh5 data to omf element."""
        with fetch_h5_handle(self.geoh5):
            kwargs = self.collect_h5_attributes(**kwargs)
            uid = kwargs.pop("uid")

            if self.entity.association.name == "VERTEX":
                kwargs["location"] = "vertices"
            else:
                kwargs["location"] = _ASSOCIATION_MAP[type(self.entity.parent)]

            self._element = self.omf_type(**kwargs)
            self._element._backend.update({"uid": uid})  # pylint: disable=W0212

        return self._element


class ElementConversion(BaseConversion):
    """
    Conversion between :obj:`omf.pointset.PointSetElement` and
    :obj:`geoh5py.objects.Points`
    """

    _attribute_map: dict[str, Any] = {
        "name": "name",
        "uid": "uid",
        "geometry": None,
    }

    def __init__(self, obj: UidModel | Entity, geoh5: str | Path | Workspace):
        super().__init__(obj, geoh5)

        if isinstance(obj, UidModel):
            self.geoh5_type = _CLASS_MAP[type(self.element.geometry)]

    def from_omf(self, **kwargs) -> Entity | None:
        """Convert omf element to geoh5 entity."""
        with fetch_h5_handle(self.geoh5) as workspace:
            try:
                kwargs = self.collect_omf_attributes(**kwargs)
            except OMFtoGeoh5NotImplemented as error:
                warnings.warn(error.args[0])
                return None

            self._entity = workspace.create_entity(
                self.geoh5_type, **{"entity": kwargs}
            )
            self.process_dependents(self.element, workspace)

        return self._entity

    def from_geoh5(self, **kwargs) -> UidModel:
        """Convert a geoh5 entity to omf element."""
        with fetch_h5_handle(self.geoh5) as workspace:
            kwargs = self.collect_h5_attributes(**kwargs)
            uid = kwargs.pop("uid")
            self._element = self.omf_type(**kwargs)
            self._element._backend.update({"uid": uid})  # pylint: disable=W0212
            self._element.data = self.process_dependents(self.entity, workspace)

        return self._element


class GeometryConversion(BaseConversion):
    def from_omf(self, **kwargs) -> dict:
        """Generate a dictionary of arguments from omf element."""
        kwargs = self.collect_omf_attributes(**kwargs)
        return kwargs

    def from_geoh5(self, **kwargs) -> UidModel:
        """Generate an omf element from geoh5 attributes."""
        with fetch_h5_handle(self.geoh5):
            kwargs = self.collect_h5_attributes(**kwargs)

        return self.omf_type(**kwargs)


class ProjectConversion(BaseConversion):
    """
    Conversion between a :obj:`omf.base.Project` and :obj:`geoh5py.groups.RootGroup`
    """

    omf_type = Project
    geoh5_type = RootGroup
    _attribute_map: dict = {
        "uid": "uid",
        "name": "name",
        "units": "distance_unit",
        "revision": "version",
    }

    def from_omf(self, **kwargs) -> RootGroup:
        """Convert omf element to geoh5 entity."""
        with fetch_h5_handle(self.geoh5) as workspace:
            kwargs = self.collect_omf_attributes(**kwargs)
            self._entity: RootGroup = workspace.root

            for key, value in kwargs.items():
                setattr(self._entity, key, value)

            self.process_dependents(self._element, workspace)

        return self._entity

    def from_geoh5(self, **kwargs) -> Project:
        """Convert RootGroup to omf Project."""
        with fetch_h5_handle(self.geoh5) as workspace:
            kwargs = self.collect_h5_attributes(**kwargs)
            uid = kwargs.pop("uid")
            self._element = self.omf_type(**kwargs)
            self._element._backend.update({"uid": uid})  # pylint: disable=W0212
            self._element.elements = self.process_dependents(self.entity, workspace)

        return self._element


class ValuesConversion(BaseConversion):
    """
    Conversion between :obj:`omf.data.ScalarArray` and
    :obj:`geoh5py.data.Data.values`
    """

    omf_type = ScalarArray
    geoh5_type = np.ndarray
    _attribute_map: dict = {"array": "values"}

    def from_omf(self, **kwargs) -> np.ndarray | None:
        return self.element.array

    def from_geoh5(self) -> UidModel:
        """TODO Convert geoh5 entity to omf element."""
        raise NotImplementedError


class ArrayConversion(BaseConversion):
    """
    Conversion from :obj:`omf.data.Int2Array` or `Vector3Array` to :obj:`numpy.ndarray`
    """

    omf_type = ScalarArray
    geoh5_type = np.ndarray
    _attribute_map: dict = {}

    def from_omf(self, **kwargs) -> np.ndarray | None:
        return np.c_[self.element]

    def from_geoh5(self) -> UidModel:
        """TODO Convert geoh5 entity to omf element."""
        raise NotImplementedError


class PointSetGeometryConversion(GeometryConversion):
    """
    Conversion between :obj:`omf.pointset.PointSetGeometry` and
    :obj:`geoh5py.objects.Points.vertices`
    """

    omf_type = PointSetGeometry
    geoh5_type = Points
    _attribute_map: dict = {"vertices": "vertices"}


class LineSetGeometryConversion(GeometryConversion):
    """
    Conversion between :obj:`omf.lineset.LineSetElement` and
    :obj:`geoh5py.objects.Curve` `vertices` and `cells`
    """

    omf_type = LineSetGeometry
    geoh5_type = Curve
    _attribute_map: dict = {"vertices": "vertices", "segments": "cells"}


class SurfaceGeometryConversion(GeometryConversion):
    """
    Conversion between :obj:`omf.lineset.LineSetElement` and
    :obj:`geoh5py.objects.Curve` `vertices` and `cells`
    """

    omf_type = SurfaceGeometry
    geoh5_type = Surface
    _attribute_map: dict = {"vertices": "vertices", "triangles": "cells"}


class SurfaceGridGeometryConversion(GeometryConversion):
    """
    Conversion between :obj:`omf.lineset.LineSetElement` and
    :obj:`geoh5py.objects.Curve` `vertices` and `cells`
    """

    omf_type = SurfaceGridGeometry
    geoh5_type = Grid2D
    _attribute_map: dict = {
        "u": "u",
        "v": "v",
    }

    def collect_omf_attributes(self, **kwargs):
        """Convert attributes from omf to geoh5."""
        if self.element.axis_v[-1] != 0:
            raise OMFtoGeoh5NotImplemented(
                f"{SurfaceGridGeometry} with 3D rotation axes."
            )

        for key, alias in self._attribute_map.items():
            tensor = getattr(self.element, f"tensor_{key}")
            if len(np.unique(tensor)) > 1:
                raise OMFtoGeoh5NotImplemented(
                    f"{SurfaceGridGeometry} with variable cell sizes along the {key} axis."
                )

            kwargs.update(
                {f"{alias}_cell_size": tensor[0], f"{alias}_count": len(tensor)}
            )

        azimuth = (
            450 - np.rad2deg(np.arctan2(self.element.axis_v[1], self.element.axis_v[0]))
        ) % 360

        if azimuth != 0:
            kwargs.update({"rotation": azimuth})

        if self.element.axis_u[-1] != 0:
            dip = np.rad2deg(
                np.arcsin(self.element.axis_u[-1] / np.linalg.norm(self.element.axis_u))
            )
            kwargs.update({"dip": dip})

        return kwargs


class VolumeGridGeometryConversion(GeometryConversion):
    """
    Conversion between :obj:`omf.lineset.LineSetElement` and
    :obj:`geoh5py.objects.Curve` `vertices` and `cells`
    """

    omf_type = VolumeGridGeometry
    geoh5_type = BlockModel
    _attribute_map: dict = {"u": "u", "v": "v", "w": "z"}

    def collect_omf_attributes(self, **kwargs) -> dict:

        if not np.allclose(np.cross(self.element.axis_w, [0, 0, 1]), [0, 0, 0]):
            raise OMFtoGeoh5NotImplemented(
                f"{VolumeGridGeometry} with 3D rotation axes."
            )

        for key, alias in self._attribute_map.items():
            tensor = getattr(self.element, f"tensor_{key}")
            cell_delimiter = np.r_[0, np.cumsum(tensor)]
            kwargs.update({f"{alias}_cell_delimiters": cell_delimiter})

        azimuth = (
            450 - np.rad2deg(np.arctan2(self.element.axis_v[1], self.element.axis_v[0]))
        ) % 360

        if azimuth != 0:
            kwargs.update({"rotation": azimuth})

        kwargs.update({"origin": np.r_[self.element.origin]})

        return kwargs


class PointsConversion(ElementConversion):
    """
    Conversion between :obj:`omf.pointset.PointSetElement` and
    :obj:`geoh5py.objects.Points`
    """

    omf_type = PointSetElement
    geoh5_type: Points
    _attribute_map = ElementConversion._attribute_map.copy()
    _attribute_map["geometry"] = PointSetGeometryConversion


class CurveConversion(ElementConversion):
    """
    Conversion between :obj:`omf.lineset.LineSetElement` and
    :obj:`geoh5py.objects.Curve`
    """

    omf_type = LineSetElement
    geoh5_type: Curve


class SurfaceConversion(ElementConversion):
    """
    Conversion between :obj:`omf.lineset.LineSetElement` and
    :obj:`geoh5py.objects.Curve`
    """

    omf_type = SurfaceElement
    geoh5_type: Surface | Grid2D


class VolumeConversion(ElementConversion):
    """
    Conversion between :obj:`omf.volume.VolumeElement` and
    :obj:`geoh5py.objects.BlockModel`
    """

    omf_type = VolumeElement
    geoh5_type: BlockModel


@contextmanager
def fetch_h5_handle(file: str | Workspace | Path, mode: str = "a") -> Workspace:
    """
    Open in read+ mode a geoh5 file from string.
    If receiving a file instead of a string, merely return the given file.

    :param file: Name or handle to a geoh5 file.
    :param mode: Set the h5 read/write mode

    :return h5py.File: Handle to an opened h5py file.
    """
    if isinstance(file, Workspace):
        try:
            yield file
        finally:
            pass
    else:
        if Path(file).suffix != ".geoh5":
            raise ValueError("Input h5 file must have a 'geoh5' extension.")

        h5file = Workspace(file, mode)

        try:
            yield h5file
        finally:
            h5file.close()


_ASSOCIATION_MAP = {
    Curve: "segments",
    Surface: "faces",
    Grid2D: "faces",
    BlockModel: "cells",
}

_CLASS_MAP = {
    PointSetGeometry: Points,
    LineSetGeometry: Curve,
    SurfaceGeometry: Surface,
    SurfaceGridGeometry: Grid2D,
    VolumeGridGeometry: BlockModel,
}

_CONVERSION_MAP = {
    FloatData: DataConversion,
    Int2Array: ArrayConversion,
    LineSetElement: CurveConversion,
    LineSetGeometry: LineSetGeometryConversion,
    Points: PointsConversion,
    PointSetElement: PointsConversion,
    PointSetGeometry: PointSetGeometryConversion,
    Project: ProjectConversion,
    RootGroup: ProjectConversion,
    ScalarArray: ValuesConversion,
    ScalarData: DataConversion,
    SurfaceElement: SurfaceConversion,
    SurfaceGeometry: SurfaceGeometryConversion,
    SurfaceGridGeometry: SurfaceGridGeometryConversion,
    Vector3Array: ArrayConversion,
    VolumeElement: VolumeConversion,
    VolumeGridGeometry: VolumeGridGeometryConversion,
}
