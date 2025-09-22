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
"""OpenTelemetry auto-instrumentation for EODAG."""

from typing import Collection

from eodag import EODataAccessGateway
from opentelemetry.instrumentation.eodag import metrics, tracing
from opentelemetry.instrumentation.eodag.package import _instruments
from opentelemetry.instrumentation.instrumentor import BaseInstrumentor
from opentelemetry.metrics import get_meter
from opentelemetry.trace import get_tracer


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

    def instrumentation_dependencies(self) -> Collection[str]:
        """Return a list of python packages with versions that the will be instrumented.

        :returns: The list of instrumented python packages.
        :rtype: Collection[str]
        """
        return _instruments

    def _instrument(self, **kwargs) -> None:
        """Instruments EODAG."""
        tracer_provider = kwargs.get("tracer_provider")
        tracer = get_tracer(__name__, tracer_provider=tracer_provider)

        tracing.patch_eodag(tracer)

        meter_provider = kwargs.get("meter_provider")
        meter = get_meter(__name__, meter_provider=meter_provider)

        metrics.init_and_patch(meter, self._eodag_api)

    def _uninstrument(self, **kwargs) -> None:
        """Uninstrument the library.

        This only works if no other module also patches eodag.
        """
        metrics.remove_patches()
        tracing.remove_patches()
