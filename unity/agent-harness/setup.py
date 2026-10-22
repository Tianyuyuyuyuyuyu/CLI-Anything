import os

from setuptools import setup, find_namespace_packages

_here = os.path.abspath(os.path.dirname(__file__))
_readme_path = os.path.join(_here, "cli_anything", "unity", "README.md")
_long_description = ""
if os.path.exists(_readme_path):
    with open(_readme_path, encoding="utf-8") as fh:
        _long_description = fh.read()

setup(
    name="cli-anything-unity",
    version="1.0.0",
    description=(
        "Agent-native CLI for Unity Editor: project management, batch-mode "
        "script execution, test runs, player builds, and logs."
    ),
    long_description=_long_description,
    long_description_content_type="text/markdown",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
        "defusedxml>=0.7.1",
    ],
    extras_require={
        "dev": ["pytest>=7.0.0", "pytest-cov>=4.0.0"],
    },
    entry_points={
        "console_scripts": [
            "cli-anything-unity=cli_anything.unity.unity_cli:main",
        ],
    },
    package_data={
        "cli_anything.unity": ["skills/*.md", "README.md"],
    },
)
