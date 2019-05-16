"""
Functions handling output formats.

This module deserves a cleaner rewrite some day.
"""

import logging
import os
import pkg_resources
from pprint import pformat
from rasterio.crs import CRS
import warnings

from mapchete.errors import MapcheteConfigError, MapcheteDriverError
from mapchete.io import read_json, write_json
from mapchete.tile import BufferedTilePyramid

DRIVERS_ENTRY_POINT = "mapchete.formats.drivers"


logger = logging.getLogger(__name__)

_FILE_EXT_TO_DRIVER = {}


def _file_ext_to_driver():
    global _FILE_EXT_TO_DRIVER
    if _FILE_EXT_TO_DRIVER:
        return _FILE_EXT_TO_DRIVER
    else:
        _FILE_EXT_TO_DRIVER = {}
        for v in pkg_resources.iter_entry_points(DRIVERS_ENTRY_POINT):
            _driver = v.load()
            if hasattr(_driver, "METADATA"):
                metadata = v.load().METADATA
                try:
                    driver_name = metadata["driver_name"]
                    for ext in metadata["file_extensions"]:
                        _FILE_EXT_TO_DRIVER[ext] = (
                            _FILE_EXT_TO_DRIVER[ext] + driver_name
                            if ext in _FILE_EXT_TO_DRIVER
                            else [driver_name]
                        )
                except Exception:
                    pass
        if not _FILE_EXT_TO_DRIVER:
            raise MapcheteDriverError("no drivers could be found")
        return _FILE_EXT_TO_DRIVER


def available_output_formats():
    """
    Return all available output formats.

    Returns
    -------
    formats : list
        all available output formats
    """
    output_formats = []
    for v in pkg_resources.iter_entry_points(DRIVERS_ENTRY_POINT):
        driver_ = v.load()
        if hasattr(driver_, "METADATA") and (driver_.METADATA["mode"] in ["w", "rw"]):
            output_formats.append(driver_.METADATA["driver_name"])
    return output_formats


def available_input_formats():
    """
    Return all available input formats.

    Returns
    -------
    formats : list
        all available input formats
    """
    input_formats = []
    for v in pkg_resources.iter_entry_points(DRIVERS_ENTRY_POINT):
        logger.debug("driver found: %s", v)
        driver_ = v.load()
        if hasattr(driver_, "METADATA") and (driver_.METADATA["mode"] in ["r", "rw"]):
            input_formats.append(driver_.METADATA["driver_name"])
    return input_formats


def load_output_reader(output_params):
    """
    Return OutputReader class of driver.

    Returns
    -------
    output : ``OutputDataReader``
        output reader object
    """
    if not isinstance(output_params, dict):
        raise TypeError("output_params must be a dictionary")
    driver_name = output_params["format"]
    for v in pkg_resources.iter_entry_points(DRIVERS_ENTRY_POINT):
        _driver = v.load()
        if all(
            [hasattr(_driver, attr) for attr in ["OutputDataReader", "METADATA"]]
            ) and (
            _driver.METADATA["driver_name"] == driver_name
        ):
            return _driver.OutputDataReader(output_params)
    raise MapcheteDriverError("no loader for driver '%s' could be found." % driver_name)


def load_output_writer(output_params, readonly=False):
    """
    Return output class of driver.

    Returns
    -------
    output : ``OutputDataWriter``
        output writer object
    """
    if not isinstance(output_params, dict):
        raise TypeError("output_params must be a dictionary")
    driver_name = output_params["format"]
    for v in pkg_resources.iter_entry_points(DRIVERS_ENTRY_POINT):
        _driver = v.load()
        if all(
            [hasattr(_driver, attr) for attr in ["OutputDataWriter", "METADATA"]]
            ) and (
            _driver.METADATA["driver_name"] == driver_name
        ):
            return _driver.OutputDataWriter(output_params, readonly=readonly)
    raise MapcheteDriverError("no loader for driver '%s' could be found." % driver_name)


def load_input_reader(input_params, readonly=False):
    """
    Return input class of driver.

    Returns
    -------
    input_params : ``InputData``
        input parameters
    """
    logger.debug("find input reader with params %s", input_params)
    if not isinstance(input_params, dict):
        raise TypeError("input_params must be a dictionary")
    if "abstract" in input_params:
        driver_name = input_params["abstract"]["format"]
    elif "path" in input_params:
        if os.path.splitext(input_params["path"])[1]:
            input_file = input_params["path"]
            driver_name = driver_from_file(input_file)
        else:
            logger.debug("%s is a directory", input_params["path"])
            driver_name = "TileDirectory"
    else:
        raise MapcheteDriverError("invalid input parameters %s" % input_params)
    for v in pkg_resources.iter_entry_points(DRIVERS_ENTRY_POINT):
        driver_ = v.load()
        if hasattr(driver_, "METADATA") and (
            driver_.METADATA["driver_name"] == driver_name
        ):
            return v.load().InputData(input_params, readonly=readonly)
    raise MapcheteDriverError("no loader for driver '%s' could be found." % driver_name)


def driver_from_file(input_file):
    """
    Guess driver from file extension.

    Returns
    -------
    driver : string
        driver name
    """
    file_ext = os.path.splitext(input_file)[1].split(".")[1]
    if file_ext not in _file_ext_to_driver():
        raise MapcheteDriverError(
            "no driver could be found for file extension %s" % file_ext
        )
    driver = _file_ext_to_driver()[file_ext]
    if len(driver) > 1:
        warnings.warn("more than one driver for file found, taking %s" % driver[0])
    return driver[0]


def params_to_dump(params):
    # in case GridDefinition was not yet initialized
    return dict(
        pyramid=BufferedTilePyramid(
            grid=params["grid"],
            tile_size=params.get("tile_size", 256),
            metatiling=params.get("metatiling", 1),
            pixelbuffer=params.get("pixelbuffer", 0),
        ).to_dict(),
        driver={
           k: v
           for k, v in params.items()
           if k not in ["path", "grid", "pixelbuffer", "metatiling"]
        }
    )


def read_output_metadata(metadata_json):
    params = read_json(metadata_json)
    grid = params["pyramid"]["grid"]
    if grid["type"] == "geodetic" and grid["shape"] == [2, 1]:
        warnings.warn(
            DeprecationWarning(
                "Deprecated grid shape ordering found. "
                "Please change grid shape from [2, 1] to [1, 2] in %s."
                % metadata_json
            )
        )
        params["pyramid"]["grid"]["shape"] = [1, 2]
    if "crs" in grid and isinstance(grid["crs"], str):
        crs = CRS.from_string(grid["crs"])
        warnings.warn(
            DeprecationWarning(
                "Deprecated 'srs' found in %s: '%s'. "
                "Use WKT representation instead: %s" % (
                    metadata_json, grid["crs"], pformat(dict(wkt=crs.to_wkt()))
                )
            )
        )
        params["pyramid"]["grid"].update(srs=dict(wkt=crs.to_wkt()))
    params.update(
        pyramid=BufferedTilePyramid(
            params["pyramid"]["grid"],
            metatiling=params["pyramid"].get("metatiling", 1),
            tile_size=params["pyramid"].get("tile_size", 256),
            pixelbuffer=params["pyramid"].get("pixelbuffer", 0)
        )
    )
    return params


def write_output_metadata(output_params):
    """Dump output JSON and verify parameters if output metadata exist."""
    if "path" in output_params:
        metadata_path = os.path.join(output_params["path"], "metadata.json")
        logger.debug("check for output %s", metadata_path)
        try:
            existing_params = read_output_metadata(metadata_path)
            logger.debug("%s exists", metadata_path)
            logger.debug("existing output parameters: %s", pformat(existing_params))
            existing_tp = existing_params["pyramid"]
            current_params = params_to_dump(output_params)
            logger.debug("current output parameters: %s", pformat(current_params))
            current_tp = BufferedTilePyramid(**current_params["pyramid"])
            if existing_tp != current_tp:
                raise MapcheteConfigError(
                    "pyramid definitions between existing and new output do not match: "
                    "%s != %s" % (existing_tp, current_tp)
                )
            existing_format = existing_params["driver"]["format"]
            current_format = current_params["driver"]["format"]
            if existing_format != current_format:
                raise MapcheteConfigError(
                    "existing output format does not match new output format: "
                    "%s != %s" % (
                        (existing_format, current_format)
                    )
                )
        except FileNotFoundError:
            logger.debug("%s does not exist", metadata_path)
            dump_params = params_to_dump(output_params)
            # dump output metadata
            write_json(metadata_path, dump_params)
