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
"""Tracing module for EODAG auto-instrumentation."""

import functools

from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer


def traced_method(tracer: Tracer, span_name: str):
    """Decorator to trace a method with OpenTelemetry."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(
                span_name, kind=SpanKind.INTERNAL
            ) as span:
                # Ajoute tous les kwargs comme attributs
                for k, v in kwargs.items():
                    if isinstance(v, (str, int, float, bool)):
                        span.set_attribute(f"kwarg.{k}", v)
                    else:
                        span.set_attribute(f"kwarg.{k}", f"<{type(v).__name__}>")
                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    raise

        wrapper._otel_tracing_applied = True
        return wrapper

    return decorator


def patch_eodag(tracer: Tracer):
    """Patch EODAGPluginMount and EODataAccessGateway public methods for tracing."""
    from eodag.api.core import EODataAccessGateway
    from eodag.plugins.manager import PluginManager

    # Patch EODAGPluginMount to trace plugin public methods
    orig_build_plugin = PluginManager._build_plugin

    def new_build_plugin(*args, **kwargs):
        """Wrap all public methods of a plugin instance with tracing."""
        plugin = orig_build_plugin(*args, **kwargs)
        for attr_name in dir(plugin):
            if attr_name.startswith("_"):
                continue
            attr = getattr(plugin, attr_name)
            if callable(attr) and not getattr(attr, "_otel_tracing_applied", False):
                wrapped = traced_method(
                    tracer, f"{plugin.__class__.__name__}.{attr_name}"
                )(attr)
                setattr(plugin, attr_name, wrapped)

        return plugin

    PluginManager._build_plugin = new_build_plugin

    # Patch EODataAccessGateway to trace core public methods
    for attr_name in dir(EODataAccessGateway):
        if attr_name.startswith("_"):
            continue
        attr = getattr(EODataAccessGateway, attr_name)
        if callable(attr) and not getattr(attr, "_otel_tracing_applied", False):
            wrapped = traced_method(tracer, f"EODataAccessGateway.{attr_name}")(attr)
            setattr(EODataAccessGateway, attr_name, wrapped)


def remove_patches():
    """Restore the original EODAGPluginMount.__init__ method if patched."""
    from eodag.api.core import EODataAccessGateway
    from eodag.plugins.base import EODAGPluginMount

    # Restore EODAGPluginMount.__init__
    orig_init = getattr(EODAGPluginMount.__init__, "__wrapped__", None)
    if orig_init is not None:
        EODAGPluginMount.__init__ = orig_init

    # Restore EODataAccessGateway public methods if patched
    for attr_name in dir(EODataAccessGateway):
        if attr_name.startswith("_"):
            continue
        attr = getattr(EODataAccessGateway, attr_name)
        orig_method = getattr(attr, "__wrapped__", None)
        if orig_method is not None:
            setattr(EODataAccessGateway, attr_name, orig_method)
