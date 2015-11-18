from setuptools import setup

setup(
    name='openbci-wamp',
    version='0.0.1',
    description="'OpenBCI' WAMP Component",
    platforms=['Any'],
    packages=['openbci'],
    include_package_data=True,
    zip_safe=False,
    entry_points={
        'autobahn.twisted.wamplet': [
            'backend = openbci:OpenBCIComponent'
        ],
    }
)
