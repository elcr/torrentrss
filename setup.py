import setuptools

setuptools.setup(
    name='torrentrss',
    description='torrentrss',
    version='0.1.5',
    author='elcr',
    url='https://bitbucket.org/elcr/torrentrss',
    license='MIT',
    install_requires=['click', 'requests', 'feedparser', 'jsonschema'],
    packages=['torrentrss'],
    package_data={'torrentrss': ['config_example.json', 'config_schema.json']},
    entry_points={'console_scripts': ['torrentrss=torrentrss.cli:main']}
)