#!/usr/bin/env python

from os.path import abspath, dirname, join

from setuptools import setup

from src.SSHLibrary.version import VERSION

CURDIR = dirname(abspath(__file__))
REQUIREMENTS = [
    "robotframework >= 7.0",
    "paramiko >= 1.15.3",
    "scp >= 0.13.0",
]


with open(join(CURDIR, "README.rst")) as f:
    DESCRIPTION = f.read()

CLASSIFIERS = """
Development Status :: 5 - Production/Stable
License :: OSI Approved :: Apache Software License
Operating System :: OS Independent
Programming Language :: Python
Programming Language :: Python :: 3.10
Topic :: Software Development :: Testing
Framework :: Robot Framework
Framework :: Robot Framework :: Library
""".strip().splitlines()

setup(
    name="robotframework-sshlibrary",
    version=VERSION,
    description="Robot Framework test library for SSH and SFTP",
    long_description=DESCRIPTION,
    author="Robot Framework Developers",
    author_email="robotframework@gmail.com",
    url="https://github.com/robotframework/SSHLibrary",
    license="Apache License 2.0",
    keywords="robotframework testing testautomation ssh sftp",
    platforms="any",
    classifiers=CLASSIFIERS,
    install_requires=REQUIREMENTS,
    package_dir={"": "src"},
    packages=["SSHLibrary"],
)
