""""
Setup the Integration.

It does so by using stages, each stage is responsible for setting up the next.
* Pre-Setup:
    Validate the config.
    Make sure user input and DB is usable.

* Setup:
    Make the living_space object out of the config/DB.

* Post-Setup:
    Add defaults to scenes.

"""
