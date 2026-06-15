from setuptools import find_packages, setup

setup(
	name="uhis-next-core",
	version="0.1.0",
	description="UHIS-Next Core — multi-country public health platform, server of record and sync API provider.",
	author="Medtronic Labs",
	author_email="admin@medtroniclabs.org",
	license="GPL-3.0-or-later",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	python_requires=">=3.10",
	classifiers=[
		"License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
		"Programming Language :: Python :: 3",
		"Programming Language :: Python :: 3.10",
		"Framework :: Frappe",
	],
)
