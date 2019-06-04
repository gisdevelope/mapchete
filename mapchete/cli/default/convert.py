import click
import logging
from multiprocessing import cpu_count
import os
from pprint import pformat
import rasterio
from rasterio.dtypes import dtype_ranges
from shapely.geometry import box
import sys
import tilematrix

from mapchete.cli import utils
from mapchete.config import raw_conf, raw_conf_output_pyramid, get_zoom_levels
from mapchete.formats import (
    driver_from_file, available_output_formats, available_input_formats
)
from mapchete.io import read_json, get_best_zoom_level
from mapchete.io.vector import reproject_geometry
from mapchete.tile import BufferedTilePyramid

logger = logging.getLogger(__name__)
OUTPUT_FORMATS = available_output_formats()


@click.command(help="Convert outputs or other geodata.")
@utils.arg_input
@utils.arg_output
@utils.opt_zoom
@utils.opt_bounds
@utils.opt_point
@utils.opt_wkt_geometry
@click.option(
    "--clip-geometry", "-c", type=click.Path(exists=True),
    help="Clip output by geometry"
)
@click.option(
    "--output-pyramid", type=click.Choice(tilematrix._conf.PYRAMID_PARAMS.keys()),
    help="Output pyramid to write to."
)
@click.option(
    "--output-metatiling", "-m", type=click.INT,
    help="Output metatiling.",
)
@click.option(
    "--output-format", type=click.Choice(available_output_formats()),
    help="Output format."
)
@click.option(
    "--output-dtype", type=click.Choice(dtype_ranges.keys()),
    help="Output data type (for raster output only)."
)
@click.option(
    "--scale-ratio", type=click.FLOAT, default=1.,
    help="Scaling factor (for raster output only)."
)
@click.option(
    "--scale-offset", type=click.FLOAT, default=0.,
    help="Scaling offset (for raster output only)."
)
@utils.opt_overwrite
@utils.opt_verbose
@utils.opt_no_pbar
@utils.opt_debug
@utils.opt_multi
@utils.opt_logfile
@utils.opt_vrt
@utils.opt_idx_out_dir
def convert(
    input_,
    output,
    zoom=None,
    bounds=None,
    point=None,
    wkt_geometry=None,
    clip_geometry=None,
    output_pyramid=None,
    output_metatiling=None,
    output_format=None,
    output_dtype=None,
    scale_ratio=None,
    scale_offset=None,
    overwrite=False,
    logfile=None,
    verbose=False,
    no_pbar=False,
    debug=False,
    multi=None,
    vrt=False,
    idx_out_dir=None
):
    input_info = _get_input_info(input_)
    output_info = _get_output_info(output)

    # collect mapchete configuration
    mapchete_config = dict(
        process="mapchete.processes.convert",
        input=dict(raster=input_, clip=clip_geometry),
        pyramid=(
            dict(
                grid=output_pyramid,
                metatiling=(
                    output_metatiling or
                    (
                        input_info["pyramid"].get("metatiling", 1)
                        if input_info["pyramid"]
                        else 1
                    )
                ),
                pixelbuffer=(
                    input_info["pyramid"].get("pixelbuffer", 0)
                    if input_info["pyramid"]
                    else 0
                )
            )
            if output_pyramid
            else input_info["pyramid"]
        ),
        output=dict(
            {
                k: v
                for k, v in input_info["output_params"].items()
                if k not in ["delimiters", "bounds", "mode"]
            },
            path=output,
            format=(
                output_format or
                output_info["driver"] or
                input_info["output_params"]["format"]
            ),
            dtype=output_dtype or input_info["output_params"].get("dtype")
        ),
        config_dir=os.getcwd(),
        zoom_levels=zoom or input_info["zoom_levels"],
        scale_ratio=scale_ratio,
        scale_offset=scale_offset
    )

    # assert all required information is there
    if mapchete_config["output"]["format"] is None:
        # this happens if input file is e.g. JPEG2000 and output is a tile directory
        raise click.BadOptionUsage("output-format", "Output format required.")
    output_type = OUTPUT_FORMATS[mapchete_config["output"]["format"]]["data_type"]
    if mapchete_config["pyramid"] is None:
        raise click.BadOptionUsage("output-pyramid", "Output pyramid required.")
    elif mapchete_config["zoom_levels"] is None:
        try:
            mapchete_config.update(
                zoom_levels=dict(
                    min=0,
                    max=get_best_zoom_level(input_, mapchete_config["pyramid"]["grid"])
                )
            )
        except:
            raise click.BadOptionUsage("zoom", "Zoom levels required.")
    elif input_info["input_type"] != output_type:
        raise click.BadArgumentUsage(
            "Output format type (%s) is incompatible with input format (%s)." % (
                output_type, input_info["input_type"]
            )
        )

    # determine process bounds
    out_pyramid = BufferedTilePyramid.from_dict(mapchete_config["pyramid"])
    mapchete_config.update(
        bounds=(
            bounds or
            reproject_geometry(
                box(*input_info["bounds"]),
                src_crs=input_info["crs"],
                dst_crs=out_pyramid.crs
            ).bounds
            if input_info["bounds"]
            else out_pyramid.bounds
        )
    )
    logger.debug("temporary config generated: %s", pformat(mapchete_config))

    utils._process_area(
        debug=debug,
        mapchete_config=mapchete_config,
        mode="overwrite" if overwrite else "continue",
        zoom=zoom,
        wkt_geometry=wkt_geometry,
        point=point,
        bounds=bounds,
        multi=multi or cpu_count(),
        verbose_dst=open(os.devnull, 'w') if debug or not verbose else sys.stdout,
        no_pbar=no_pbar,
        vrt=vrt,
        idx_out_dir=idx_out_dir
    )


def _get_input_info(input_):
    # single file input can be a mapchete file or a rasterio/fiona file
    if os.path.isfile(input_):
        driver = driver_from_file(input_)

        if driver == "Mapchete":
            logger.debug("input is mapchete file")
            input_info = _input_mapchete_info(input_)

        elif driver == "raster_file":
            # this should be readable by rasterio
            logger.debug("input is raster_file")
            input_info = _input_rasterio_info(input_)

        elif driver == "vector_file":
            # this should be readable by fiona
            logger.debug("input is vector_file")
            raise NotImplementedError()

    # assuming tile directory
    else:
        logger.debug("input is tile directory")
        input_info = _input_tile_directory_info(input_)

    return input_info


def _input_mapchete_info(input_):
    conf = raw_conf(input_)
    output_params = conf["output"]
    pyramid = raw_conf_output_pyramid(conf)
    return dict(
        output_params=output_params,
        pyramid=pyramid.to_dict(),
        crs=pyramid.crs,
        zoom_levels=get_zoom_levels(process_zoom_levels=conf["zoom_levels"]),
        pixel_size=None,
        input_type=OUTPUT_FORMATS[output_params["format"]]["data_type"],
        bounds=conf.get("bounds")
    )


def _input_rasterio_info(input_):
    with rasterio.open(input_) as src:
        return dict(
            output_params=dict(
                bands=src.meta["count"],
                dtype=src.meta["dtype"],
                format=src.driver if src.driver in available_input_formats() else None
            ),
            pyramid=None,
            crs=src.crs,
            zoom_levels=None,
            pixel_size=src.transform[0],
            input_type="raster",
            bounds=src.bounds
        )


def _input_tile_directory_info(input_):
    conf = read_json(os.path.join(input_, "metadata.json"))
    pyramid = BufferedTilePyramid.from_dict(conf["pyramid"])
    return dict(
        output_params=conf["driver"],
        pyramid=pyramid.to_dict(),
        crs=pyramid.crs,
        zoom_levels=None,
        pixel_size=None,
        input_type=OUTPUT_FORMATS[conf["driver"]["format"]]["data_type"],
        bounds=None
    )


def _get_output_info(output):
    _, file_ext = os.path.splitext(output)
    if not file_ext:
        return dict(
            type="TileDirectory",
            driver=None
        )
    elif file_ext == ".tif":
        return dict(
            type="SingleFile",
            driver="GTiff"
        )
    else:
        raise TypeError("Output file extension not recognized: %s", file_ext)