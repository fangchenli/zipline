Release Process
---------------

.. include:: dev-doc-message.txt


Updating the Release Notes
~~~~~~~~~~~~~~~~~~~~~~~~~~

When we are ready to ship a new release of zipline, edit the :doc:`releases`
page. We will have been maintaining a ``whatsnew`` file while working on the release
with the new version. First, find that file in:
``docs/source/whatsnew/<version>.txt``. It will be the highest version number.
Edit the release date field to be today's date in the format:

::

   <month> <day>, <year>


for example, November 6, 2015.
Remove the active development warning from the ``whatsnew``, since it will no
longer be pending release.
Update the title of the release from "Development" to "Release x.x.x" and
update the underline of the title to match the title's width.

If you are renaming the release at this point, you'll need to git mv the file
and also update releases.rst to reference the renamed file.

To build and view the docs locally, run:

.. code-block:: bash

   $ uv run --group docs sphinx-build -b html docs/source docs/build/html
   $ {BROWSER} docs/build/html/index.html

Updating the Python stub files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type checkers and editors use `Python stub files
<https://peps.python.org/pep-0484/#stub-files>`__ for type hinting. The
:mod:`~zipline.api` namespace is populated at import time by decorators on
``TradingAlgorithm`` methods, so its functions are hidden from static analysis
tools; ``zipline/api.pyi`` makes them visible. Make sure it is up to date:

.. code-block:: bash

   $ uv run python scripts/gen_api_stub.py
   $ uv run ruff check --fix zipline/api.pyi && uv run ruff format zipline/api.pyi

The Cython extensions' ``.pyi`` stubs are maintained by hand; check that they
match any Python-visible API changes in the ``.pyx`` files since the last
release.

Updating the ``__version__``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We use `setuptools-scm <https://setuptools-scm.readthedocs.io>`__ to manage
the package version, which it derives from git tags. This keeps the version
and the tags in sync, and gives development installs fine grained version
strings.

To create a release version, tag the release commit:

.. code-block:: bash

   $ git tag <major>.<minor>.<micro>
   $ git push && git push --tags


This will push the code and the tag information.

Next, draft a new release on the `zipline releases page
<https://github.com/fangchenli/zipline/releases>`__, choose the tag you just
pushed, and publish the release.

Uploading PyPI packages
~~~~~~~~~~~~~~~~~~~~~~~

``sdist``
^^^^^^^^^

To build the ``sdist`` (source distribution) run:

.. code-block:: bash

   $ uv build --sdist


from the zipline root. This will create a gzipped tarball in ``dist/`` that
includes all the python, cython, and miscellaneous files needed to install
zipline. To test that the source dist worked correctly, ``cd`` into an empty
directory, create a new virtual environment and then run:


.. code-block:: bash

   $ pip install <zipline-root>/dist/zipline-<major>.<minor>.<micro>.tar.gz
   $ python -c 'import zipline;print(zipline.__version__)'

This should print the version we are expecting to release.

.. note::

   It is very important to both ``cd`` into a clean directory and make a clean
   virtual environment. Changing directories ensures that we have included all
   the needed files in the sdist. Using a clean environment ensures that we
   have listed all the required packages.

Now that we have tested the package locally, it should be tested using the test
PyPI server:

.. code-block:: bash

   $ uv publish --publish-url https://test.pypi.org/legacy/ dist/zipline-<version-number>.tar.gz

This will upload zipline to the PyPI test server. To test installing from it,
create a new virtual environment, ``cd`` into a clean directory and then run:

.. code-block:: bash

   $ pip install --extra-index-url https://test.pypi.org/simple zipline
   $ python -c 'import zipline;print(zipline.__version__)'


This should pull the package you just uploaded and then print the version
number.

Now that we have tested locally and on PyPI test, it is time to upload to PyPI:

.. code-block:: bash

   $ uv publish dist/zipline-<version-number>.tar.gz

``bdist``
^^^^^^^^^

Extensions built against numpy 2 work with any numpy 2.x release, so binary
wheels can be built with ``uv build --wheel``. Wheels are specific to a
platform and Python version; publishing them for every supported combination
is best done from CI (for example with ``cibuildwheel``).

Documentation
~~~~~~~~~~~~~

To publish the documentation to the ``gh-pages`` branch, check out the latest
master and run:

.. code-block:: bash

    $ uv run --group docs python docs/deploy.py

This will build the documentation, checkout a fresh copy of the ``gh-pages``
git branch, and copy the built docs into the zipline root.

Now, using our browser of choice, view the ``index.html`` page and verify that
the docs look correct.

Once we are happy, push the updated docs to the GitHub ``gh-pages`` branch.

.. code-block:: bash

   $ git add .
   $ git commit -m "DOC: update the documentation"
   $ git push origin gh-pages

Next Commit
~~~~~~~~~~~

Push a new commit post-release that adds the ``whatsnew`` for the next release,
which should be titled according to a micro version increment. If that next
release turns out to be a major/minor version increment, the file can be
renamed when that's decided. You can use ``docs/source/whatsnew/skeleton.txt``
as a template for the new file.

Include the ``whatsnew`` file in ``docs/source/releases.rst``. New releases should
appear at the top. The syntax for this is:

::

   .. include:: whatsnew/<version>.txt
