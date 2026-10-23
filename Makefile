
.PHONY: mig makemigrations

PYTHON ?= python

mig:
	$(PYTHON) manage.py makemigrations
	$(PYTHON) manage.py migrate