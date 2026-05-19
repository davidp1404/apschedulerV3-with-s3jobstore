ARCHIVE = s3-jobstore.tgz
VERSION ?= 3.9.0.post1

FILES = \
	Makefile \
	setup.cfg \
	setup.py \
	apscheduler/jobstores/s3.py \
	sample1.py \
	s3-jobstore.md \
	tests/test_jobstores_s3.py \
	tests/test_s3_e2e.py \
	openspec \
	.kiro

.PHONY: test run wheel clean

test:
	pip install -e ".[s3,testing]" moto -q
	python -m pytest tests/test_jobstores_s3.py tests/test_s3_e2e.py -p no:tornado --no-cov -v

run:
	pip install -e ".[s3]" -q
	python -u sample1.py

wheel:
	pip install build -q
	SETUPTOOLS_SCM_PRETEND_VERSION=$(VERSION) python -m build --wheel
	@echo "Wheel created in dist/ with version $(VERSION)"

clean:
	rm -rf $(ARCHIVE) dist build *.egg-info
