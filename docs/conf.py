
import os, sys
sys.path.insert(0, os.path.abspath('..'))
project = "genro-mail-proxy"
author = "Softwell S.r.l. & Giovanni Porcari"
release = "0.1.0"
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.graphviz",
    "sphinxcontrib.mermaid",
]
templates_path = ["_templates"]
exclude_patterns = []
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_logo = "_static/logo.png"
html_title = project
napoleon_google_docstring = True
intersphinx_mapping = {"python": ("https://docs.python.org/3", None), "fastapi": ("https://fastapi.tiangolo.com", None)}
autodoc_default_options = {"members": True, "undoc-members": True, "show-inheritance": True}
