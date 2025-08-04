# -*- coding: utf-8 -*-
# Copyright 2023, CS GROUP - France, https://www.csgroup.eu/
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
"""OpenTelemetry auto-instrumentation for stac fastapi eodag."""

import functools
import logging
from timeit import default_timer
from typing import Any, Callable, Collection, Dict, Iterable, List, Optional, Union

from requests import Response
from shapely.geometry.base import BaseGeometry

from eodag import EODataAccessGateway
from eodag.api.search_result import SearchResult
from eodag.plugins.search import PreparedSearch
from eodag.plugins.search.qssearch import QueryStringSearch
from eodag.utils import DEFAULT_ITEMS_PER_PAGE, DEFAULT_PAGE
from opentelemetry.instrumentation.eodag.package import _instruments
from opentelemetry.instrumentation.instrumentor import BaseInstrumentor
from opentelemetry.metrics import (
    CallbackOptions,
    Counter,
    Histogram,
    Observation,
    get_meter,
)
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer, get_tracer
from opentelemetry.util import types

logger = logging.getLogger("eodag.utils.instrumentation.eodag")


class OverheadTimer:
    """Timer class  to calculate the overhead of a task relative to other sub-tasks.

    The main task starts and stops the global timer with the start_global_timer and
    stop_global_timer functions. The sub-tasks record their time with the
    record_subtask_time function.
    """

    # All the timer are in seconds
    _start_global_timestamp: Optional[float] = None
    _end_global_timestamp: Optional[float] = None
    _subtasks_time: float = 0.0

    def start_global_timer(self) -> None:
        """Start the timer of the main task."""
        self._start_global_timestamp = default_timer()
        self._subtasks_time = 0.0

    def stop_global_timer(self) -> None:
        """Stop the timer of the main task."""
        self._end_global_timestamp = default_timer()

    def record_subtask_time(self, time: float):
        """Record the execution time of a subtask.

        :param time: Duration of the subtask in seconds.
        :type time: float
        """
        self._subtasks_time += time

    def get_global_time(self) -> float:
        """Return the execution time of the main task.

        :returns: The global execution time in seconds.
        :rtype: float
        """
        if not self._end_global_timestamp or not self._start_global_timestamp:
            return 0.0
        return self._end_global_timestamp - self._start_global_timestamp

    def get_subtasks_time(self) -> float:
        """Return the cumulative time of the sub-tasks.

        :returns: The sub-tasks execution time in seconds.
        :rtype: float
        """
        return self._subtasks_time

    def get_overhead_time(self) -> float:
        """Return the overhead time of the main task relative to the sub-tasks.

        :returns: The overhead time in seconds.
        :rtype: float
        """
        return self.get_global_time() - self._subtasks_time


overhead_timers: Dict[int, OverheadTimer] = {}
trace_attributes: Dict[int, Any] = {}


def _instrument_search(
    tracer: Tracer,
    searched_product_types_counter: Counter,
    request_duration_seconds: Histogram,
    outbound_request_duration_seconds: Histogram,
    request_overhead_duration_seconds: Histogram,
) -> None:
    """Add the instrumentation for search operations.

    :param tracer: OpenTelemetry tracer.
    :type tracer: Tracer
    :param searched_product_types_counter: Searched product types counter.
    :type searched_product_types_counter: Counter
    :param request_duration_seconds: Request duration histogram.
    :type request_duration_seconds: Histogram
    :param outbound_request_duration_seconds: Outbound request duration histogram.
    :type outbound_request_duration_seconds: Histogram
    :param request_overhead_duration_seconds: EODAG overhead histogram.
    :type request_overhead_duration_seconds: Histogram
    """
    from eodag.api.core import EODataAccessGateway as dag

    # wrapping dag.search
    wrapped_dag__search = dag.search

    @functools.wraps(wrapped_dag__search)
    def wrapper_dag__search(
        self,
        page: int = DEFAULT_PAGE,
        items_per_page: int = DEFAULT_ITEMS_PER_PAGE,
        raise_errors: bool = False,
        start: Optional[str] = None,
        end: Optional[str] = None,
        geom: Optional[Union[str, dict[str, float], BaseGeometry]] = None,
        locations: Optional[dict[str, str]] = None,
        provider: Optional[str] = None,
        count: bool = False,
        **kwargs: Any,
    ) -> SearchResult:
        span_name = "core-search"
        attributes: types.Attributes = {
            "operation": "search",
            "product_type": kwargs.get("productType"),
        }

        with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT, attributes=attributes) as span:
            exception = None
            trace_id = span.get_span_context().trace_id
            timer = OverheadTimer()
            overhead_timers[trace_id] = timer
            trace_attributes[trace_id] = attributes
            timer.start_global_timer()

            # Call wrapped function
            try:
                result = wrapped_dag__search(
                    self,
                    page=page,
                    items_per_page=items_per_page,
                    raise_errors=raise_errors,
                    start=start,
                    end=end,
                    geom=geom,
                    locations=locations,
                    provider=provider,
                    count=count,
                    **kwargs,
                )
            except Exception as exc:
                exception = exc
            finally:
                timer.stop_global_timer()

            # Retrieve possible updated attributes
            attributes = trace_attributes[trace_id]
            span.set_attributes(attributes)

            # Product type counter
            searched_product_types_counter.add(1, {"product_type": kwargs.get("productType")})

            # Duration histograms
            request_duration_seconds.record(timer.get_global_time(), attributes=attributes)
            overhead_attributes = {k: v for k, v in attributes.items() if k != "product_type"}
            request_overhead_duration_seconds.record(timer.get_overhead_time(), attributes=overhead_attributes)
            del overhead_timers[trace_id]
            del trace_attributes[trace_id]

            if exception is not None:
                raise exception.with_traceback(exception.__traceback__)

        return result

    wrapper_dag__search.opentelemetry_instrumentation_eodag_applied = True
    dag.search = wrapper_dag__search

    # Wrapping QueryStringSearch

    wrapped_qssearch_request = QueryStringSearch._request

    @functools.wraps(wrapped_qssearch_request)
    def wrapper_qssearch_request(
        self: QueryStringSearch,
        prep: PreparedSearch,
    ) -> Response:
        span_name = "core-search"
        attributes = {"provider": self.provider}

        with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT, attributes=attributes) as span:
            exception = None
            trace_id = span.get_span_context().trace_id
            # Note: `overhead_timers` and `trace_attributes` are populated on a search or
            # download operation.
            # If this wrapper is called after a different operation, then both `timer` and
            # `parent_attributes` are not available and no metric is generated.
            timer = overhead_timers.get(trace_id)
            parent_attributes = trace_attributes.get(trace_id)
            if parent_attributes:
                parent_attributes["provider"] = self.provider
                # Get the EODAG's product type from the parent
                attributes = parent_attributes

            start_time = default_timer()

            # Call wrapped function
            try:
                result = wrapped_qssearch_request(self, prep)
            except Exception as exc:
                exception = exc
                if exception.status_code:
                    attributes["status_code"] = exception.status_code
            finally:
                elapsed_time = default_timer() - start_time

            # Duration histograms
            if timer:
                timer.record_subtask_time(elapsed_time)
                outbound_request_duration_seconds.record(elapsed_time, attributes=attributes)

            if exception is not None:
                raise exception.with_traceback(exception.__traceback__)

        return result

    wrapper_qssearch_request.opentelemetry_instrumentation_eodag_applied = True
    QueryStringSearch._request = wrapper_qssearch_request


def _create_stream_download_wrapper(
    module_name: str,
    class_name: str,
    tracer: Tracer,
    downloaded_data_counter: Counter,
    request_duration_seconds: Histogram,
    number_downloads_counter: Histogram,
) -> Callable[..., Any]:
    """Create a wrapper for _stream_download_dict methods with common instrumentation logic."""

    def _count(iter: Iterable[bytes], attributes: dict[str, Any]) -> Iterable[bytes]:
        for chunk in iter:
            increment = len(chunk)
            downloaded_data_counter.add(increment, attributes=attributes)
            yield chunk

    def wrapper(wrapped, _, args, kwargs):
        product = args[0]

        # Common labels for all metrics
        labels = {
            "provider": product.provider,
            "product_type": product.properties.get("alias") or product.product_type,
        }

        number_downloads_counter.add(1, labels)

        # Create span
        with tracer.start_as_current_span(
            f"{module_name}.{class_name}._stream_download_dict", attributes=labels
        ) as span:
            start_time = default_timer()

            try:
                # Execute the original method
                result = wrapped(*args, **kwargs)

                # Count bytes in the stream response
                if hasattr(result, "content") and result.content:
                    counted_content = _count(result.content, labels)
                    # Create new StreamResponse with counted content
                    new_result = type(result)(
                        content=counted_content, **{k: v for k, v in result.__dict__.items() if k != "content"}
                    )
                    result = new_result

                return result

            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            finally:
                duration = default_timer() - start_time
                request_duration_seconds.record(duration, labels)

    return wrapper


def _instrument_download(
    tracer: Tracer,
    downloaded_data_counter: Counter,
    number_downloads_counter: Counter,
    request_duration_seconds: Histogram,
    outbound_request_duration_seconds: Histogram,
    request_overhead_duration_seconds: Histogram,
) -> None:
    """Add the instrumentation for download operations.

    :param tracer: OpenTelemetry tracer.
    :type tracer: Tracer
    :param downloaded_data_counter: Downloaded data counter.
    :type downloaded_data_counter: Counter
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
                    "eodag.plugins.download.http",
                    "HTTPDownload",
                    tracer,
                    downloaded_data_counter,
                    request_duration_seconds,
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
                    "eodag.plugins.download.aws",
                    "AwsDownload",
                    tracer,
                    downloaded_data_counter,
                    request_duration_seconds,
                    number_downloads_counter,
                ),
            )
    except ImportError:
        logger.warning("Could not instrument AWS downloads: module not found")


class EODAGInstrumentor(BaseInstrumentor):
    """An instrumentor for EODAG."""

    def __init__(self, eodag_api: EODataAccessGateway = None) -> None:
        """Init the instrumentor for EODAG.

        If `eodag_api` is given, instrument also the metrics that uses a callback (currently the gauges).

        :param eodag_api: (optional) EODAG API
        :type eodag_api: EODataAccessGateway
        """
        super().__init__()
        self._eodag_api = eodag_api
        self._last_available_providers: List[str] = []
        self._last_available_product_types: List[str] = []

    def instrumentation_dependencies(self) -> Collection[str]:
        """Return a list of python packages with versions that the will be instrumented.

        :returns: The list of instrumented python packages.
        :rtype: Collection[str]
        """
        return _instruments

    def _available_providers_callback(self, options: CallbackOptions) -> Iterable[Observation]:
        """Open Telemetry callback to measure the number of available providers.

        :param options: Options for the callback.
        :type options: CallbackOptions
        :returns: The list observation.
        :rtype: Iterable[Observation]
        """
        new_available_providers: List[str] = self._eodag_api.available_providers()
        observations_dict: Dict[str, int] = {p: 0 for p in self._last_available_providers}
        for p in new_available_providers:
            observations_dict[p] = 1
        self._last_available_providers = new_available_providers
        observations = [
            Observation(
                v,
                {"provider_id": k},
            )
            for k, v in observations_dict.items()
        ]
        return observations

    def _available_product_types_callback(
        self,
        options: CallbackOptions,
    ) -> Iterable[Observation]:
        """Open Telemetry callback to measure the number of available product types.

        :param options: Options for the callback.
        :type options: CallbackOptions
        :returns: The list observation.
        :rtype: Iterable[Observation]
        """
        # Don't fetch providers to avoid rebuilding the index concurrently
        new_available_product_types: List[str] = [
            p["ID"] for p in self._eodag_api.list_product_types(fetch_providers=False)
        ]
        observations_dict: Dict[str, int] = {p: 0 for p in self._last_available_product_types}
        for p in new_available_product_types:
            observations_dict[p] = 1
        self._last_available_product_types = new_available_product_types
        observations = [
            Observation(
                v,
                {"product_type_id": k},
            )
            for k, v in observations_dict.items()
        ]
        return observations

    def _instrument(self, **kwargs) -> None:
        """Instruments EODAG."""
        tracer_provider = kwargs.get("tracer_provider")
        tracer = get_tracer(__name__, tracer_provider=tracer_provider)
        meter_provider = kwargs.get("meter_provider")
        meter = get_meter(__name__, meter_provider=meter_provider)

        if self._eodag_api:
            meter.create_observable_gauge(
                name="eodag.core.available_providers",
                callbacks=[self._available_providers_callback],
                description="The number available providers",
            )
            meter.create_observable_gauge(
                name="eodag.core.available_product_types",
                callbacks=[self._available_product_types_callback],
                description="The number available product types",
            )

        request_duration_seconds = meter.create_histogram(
            name="eodag.server.request_duration_seconds",
            unit="s",
            description="Measures the duration of the inbound HTTP request",
        )
        outbound_request_duration_seconds = meter.create_histogram(
            name="eodag.core.outbound_request_duration_seconds",
            unit="s",
            description="Measure the duration of the outbound HTTP request",
        )
        request_overhead_duration_seconds = meter.create_histogram(
            name="eodag.server.request_overhead_duration_seconds",
            unit="s",
            description="Measure the duration of the EODAG overhead on the inbound HTTP request",
        )

        downloaded_data_counter = meter.create_counter(
            name="eodag.download.downloaded_data_bytes_total",
            description="Measure data downloaded from each provider and product type",
        )
        number_downloads_counter = meter.create_counter(
            name="eodag.download.number_downloads",
            description="Number of downloads from each provider and product type",
        )

        for provider in self._eodag_api.available_providers():
            for product_type in self._eodag_api.list_product_types(provider, fetch_providers=False):
                attributes = {"provider": provider, "product_type": product_type.get("alias") or product_type["_id"]}
                downloaded_data_counter.add(0, attributes)
                number_downloads_counter.add(0, attributes)

        _instrument_download(
            tracer,
            downloaded_data_counter,
            number_downloads_counter,
            request_duration_seconds,
            outbound_request_duration_seconds,
            request_overhead_duration_seconds,
        )

        searched_product_types_counter = meter.create_counter(
            name="eodag.core.searched_product_types_total",
            description="The number of searches by provider and product type",
        )

        for product_type in self._eodag_api.list_product_types(fetch_providers=False):
            searched_product_types_counter.add(0, {"product_type": product_type["ID"]})

        _instrument_search(
            tracer,
            searched_product_types_counter,
            request_duration_seconds,
            outbound_request_duration_seconds,
            request_overhead_duration_seconds,
        )

    def _uninstrument(self, **kwargs) -> None:
        """Uninstrument the library.

        This only works if no other module also patches eodag.
        """
        import importlib

        from stac_fastapi.eodag.core import EodagCoreClient as core_client

        patches = [
            (core_client, "_search_base"),
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
