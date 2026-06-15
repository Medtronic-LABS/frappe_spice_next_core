from setuptools import setup, find_packages

setup(
    name="uhis-next-core",
    version="0.1.0",
    description="UHIS-Next Core — multi-country public health platform, server of record and sync API provider.",
    author="Medtronic Labs",
    author_email="admin@medtroniclabs.org",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    python_requires=">=3.10",
)
