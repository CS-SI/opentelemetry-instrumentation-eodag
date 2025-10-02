# -*- coding: utf-8 -*-
# Copyright 2025, CS GROUP - France, https://www.csgroup.eu/
#
# This file is part of EODAG project
#     https://www.github.com/CS-SI/EODAG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Metrics module for EODAG auto-instrumentation."""

import functools
import logging
from typing import Any, Callable, Iterable

from eodag import EODataAccessGateway
from eodag.api.search_result import SearchResult
from eodag.plugins.search.qssearch import QueryStringSearch
from opentelemetry.metrics import Counter, Histogram, Meter

logger = logging.getLogger("otel.eodag")


def safe_metrics_call(func: Callable) -> Callable:
    """Decorator to safely call metric functions without raising exceptions."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.debug(f"Metric call failed silently: {e}")

    return wrapper


def _instrument_search(
    searched_product_types_counter: Counter,
) -> None:
    """Add the instrumentation for search operations.

    :param searched_product_types_counter: Searched product types counter.
    :type searched_product_types_counter: Counter
    """
    from eodag.api.core import EODataAccessGateway as dag

    # wrapping dag._prepare_search
    wrapped_dag__prepare_search = dag._prepare_search

    @functools.wraps(wrapped_dag__prepare_search)
    def wrapper_dag__prepare_search(*args, **kwargs) -> SearchResult:
        search_plugins, prepared_kwargs = wrapped_dag__prepare_search(*args, **kwargs)

        searched_product_types_counter.add(
            1,
            {"product_type": prepared_kwargs.get("productType")} if search_plugins else "__INVALID__",
        )

        return search_plugins, prepared_kwargs

    wrapper_dag__prepare_search.opentelemetry_instrumentation_eodag_applied = True
    dag._prepare_search = wrapper_dag__prepare_search


def _create_stream_download_wrapper(
    downloaded_data_counter: Counter,
    number_downloads_counter: Histogram,
) -> Callable[..., Any]:
    """Create a wrapper for _stream_download_dict methods with common instrumentation logic."""

    safe_add_downloads = safe_metrics_call(number_downloads_counter.add)
    safe_add_data = safe_metrics_call(downloaded_data_counter.add)

    def wrapper(wrapped, _, args, kwargs):
        try:
            product = args[0]

            labels = {
                "provider": product.provider,
                "product_type": product.properties.get("alias") or product.product_type,
            }
        except Exception as exc:
            logger.debug(f"Could not extract product info for download metrics: {exc}")
            labels = {"provider": "__UNKNOWN__", "product_type": "__UNKNOWN__"}

        safe_add_downloads(1, labels)

        result = wrapped(*args, **kwargs)

        if stream := getattr(result, "content", None):

            def _counted_stream() -> Iterable[bytes]:
                for chunk in stream:
                    safe_add_data(len(chunk), attributes=labels)
                    yield chunk

            result.content = _counted_stream()

        return result

    return wrapper


def _instrument_download(downloaded_data_counter: Counter, number_downloads_counter: Counter) -> None:
    """Add the instrumentation for download operations.

    :param downloaded_data_counter: Downloaded data volume counter.
    :param number_downloads_counter: Number of downloads counter.
    """
    import importlib

    from wrapt import wrap_function_wrapper

    try:
        http_module = importlib.import_module("eodag.plugins.download.http")
        if hasattr(http_module, "HTTPDownload"):
            wrap_function_wrapper(
                http_module,
                "HTTPDownload._stream_download_dict",
                _create_stream_download_wrapper(
                    downloaded_data_counter,
                    number_downloads_counter,
                ),
            )
    except ImportError:
        logger.warning("Could not instrument HTTP downloads: module not found")

    # Instrument AWS downloads
    try:
        aws_module = importlib.import_module("eodag.plugins.download.aws")
        if hasattr(aws_module, "AwsDownload"):
            wrap_function_wrapper(
                aws_module,
                "AwsDownload._stream_download_dict",
                _create_stream_download_wrapper(
                    downloaded_data_counter,
                    number_downloads_counter,
                ),
            )
    except ImportError:
        logger.warning("Could not instrument AWS downloads: module not found")


def init_and_patch(meter: Meter, eodag_api: EODataAccessGateway) -> None:
    """Create the metrics for EODAG."""
    downloaded_data_counter = meter.create_counter(
        name="eodag.download.downloaded_data_bytes_total",
        description="Measure data downloaded from each provider and product type",
    )
    number_downloads_counter = meter.create_counter(
        name="eodag.download.number_downloads",
        description="Number of downloads from each provider and product type",
    )

    for provider in eodag_api.available_providers():
        for product_type in eodag_api.list_product_types(provider, fetch_providers=False):
            attributes = {
                "provider": provider,
                "product_type": product_type.get("alias") or product_type["_id"],
            }
            downloaded_data_counter.add(0, attributes)
            number_downloads_counter.add(0, attributes)

    _instrument_download(downloaded_data_counter, number_downloads_counter)

    searched_product_types_counter = meter.create_counter(
        name="eodag.core.searched_product_types_total",
        description="The number of searches by provider and product type",
    )

    for product_type in eodag_api.list_product_types(fetch_providers=False):
        searched_product_types_counter.add(0, {"product_type": product_type["ID"]})

    _instrument_search(searched_product_types_counter)


def remove_patches():
    """Unpatch the instrumented methods to restore original behavior."""
    import importlib

    patches = [
        (EODataAccessGateway, "search"),
        (QueryStringSearch, "_request"),
    ]
    for p in patches:
        instr_func = getattr(p[0], p[1])
        if not getattr(
            instr_func,
            "opentelemetry_instrumentation_eodag_applied",
            False,
        ):
            continue
        setattr(p[0], p[1], instr_func.__wrapped__)

    # Uninstrument download modules
    try:
        http_module = importlib.import_module("eodag.plugins.download.http")
        if hasattr(http_module, "HTTPDownload"):
            patches.append((http_module.HTTPDownload, "_stream_download_dict"))
    except ImportError:
        pass

    try:
        aws_module = importlib.import_module("eodag.plugins.download.aws")
        if hasattr(aws_module, "AwsDownload"):
            patches.append((aws_module.AwsDownload, "_stream_download_dict"))
    except ImportError:
        pass
