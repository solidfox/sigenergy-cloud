# Releasing

This package is deployed to PyPI from GitHub Releases.

## Agent Procedure

1. Update `project.version` in `pyproject.toml`.
2. Run the offline tests.
3. Commit and push the version bump and package changes to `main`.
4. Create a GitHub Release whose tag matches the package version, for example `v0.1.1`.
5. The `Upload Python Package` workflow builds the sdist/wheel and publishes to PyPI.
6. Confirm the workflow passed and that `https://pypi.org/project/sigenergy-cloud/<version>/` exists.

Do not publish to PyPI on every push. A GitHub Release is the deploy trigger.

The workflow uses PyPI Trusted Publishing through the `pypi` GitHub environment. If publishing fails with an OIDC/trusted-publisher error, configure PyPI to trust:

- owner: `solidfox`
- repository: `sigenergy-cloud`
- workflow: `python-publish.yml`
- environment: `pypi`

