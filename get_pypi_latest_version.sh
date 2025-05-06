#!/usr/bin/env bash

curl https://pypi.org/pypi/opentelemetry-instrumentation-eodag/json | python -c "import sys, json; print(json.load(sys.stdin)['info']['version']);"
