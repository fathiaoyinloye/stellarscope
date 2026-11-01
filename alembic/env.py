import os
from logging.config import fileConfig

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here for 'autogenerate support'
# from myapp.models import Base
# target_metadata = Base.metadata
# otherwise, set target_metadata to None
target_metadata = None

# other values from the .ini file can be accessed via config:
# example: my_important_option = config.get_main_option("sqlalchemy.url")
sqlalchemy_url = os.getenv("DATABASE_URL")
if sqlalchemy_url:
    config.set_main_option("sqlalchemy.url", sqlalchemy_url)
