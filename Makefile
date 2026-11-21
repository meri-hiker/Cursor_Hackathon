VENV ?= .Hack_venv
PYTHON := $(VENV)/bin/python

.DEFAULT_GOAL := help

.PHONY: help install test lint format check inventory screen

help:
	@echo "install     install this package into $(VENV)"
	@echo "test        run the test suite"
	@echo "lint        run ruff"
	@echo "format      format Python with ruff"
	@echo "check       lint and test"
	@echo "inventory   print row counts for data/"
	@echo "screen      decide every hit and write outputs/screener/dashboard.html"

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

format:
	$(PYTHON) -m ruff format src tests

check: lint test

inventory:
	$(PYTHON) -m same_person

screen:
	$(PYTHON) -m same_person.screener
