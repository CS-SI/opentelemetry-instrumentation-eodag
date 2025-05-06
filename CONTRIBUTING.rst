.. _contribute:

Contribute
==========

Thank you for considering contributing to opentelemetry-instrumentation-eodag!


Report issues
-------------

Issue tracker: https://github.com/CS-SI/opentelemetry-instrumentation-eodag/issues

Please check that a similar issue does not already exist and
include the following information in your post:

-   Describe what you expected to happen.
-   If possible, include a `minimal reproducible example`_ to help us
    identify the issue. This also helps check that the issue is not with
    your own code.
-   Describe what actually happened. Include the full traceback if there
    was an exception.
-   List your Python and opentelemetry-instrumentation-eodag versions. If possible, check if this
    issue is already fixed in the latest releases or the latest code in
    the repository.

.. _minimal reproducible example: https://stackoverflow.com/help/minimal-reproducible-example


Submit patches
--------------

If you intend to contribute to opentelemetry-instrumentation-eodag source code:

.. code-block:: bash

    git clone https://github.com/CS-SI/opentelemetry-instrumentation-eodag.git
    cd opentelemetry-instrumentation-eodag
    python -m pip install -e ".[dev]"
    pre-commit install

We use ``pre-commit`` to run a suite of linters, formatters and pre-commit hooks to
ensure the code base is homogeneously formatted and easier to read. It's important that you install it, since we run
the exact same hooks in the Continuous Integration.

To run the default test suite:

.. code-block:: bash

    tox

To run the default test suite in parallel:

.. code-block:: bash

    tox -p
