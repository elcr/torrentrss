from __future__ import annotations

import sys
from pathlib import Path

import appdirs


NAME = 'torrentrss'
VERSION = '0.9.0'
CONFIG_PATH = Path(
    appdirs.user_config_dir(appname=NAME, roaming=True),
    'config.json'
)
LOG_MESSAGE_FORMAT = '[%(asctime)s %(levelname)s] %(message)s'
COMMAND_URL_ARGUMENT = '$URL'
TORRENT_MIMETYPE = 'application/x-bittorrent'
WINDOWS = sys.platform == 'win32' or sys.platform == 'cygwin'

CONFIG_SCHEMA = {
    "$schema": "http://json-schema.org/draft-04/schema#",
    "type": "object",
    "properties": {
        "default_command": {
            "description": "The command used to execute URLs. Overridden by individual subscriptions' 'command' property. '$URL' is substituted for the torrent's URL. This array is otherwise passed directly to Python's subprocess module, which takes care of any escaping or quoting, so there is no need to do so yourself. By default the URL is launched with the operating system's default program for that protocol.",
            "type": "array",
            "items": {
                "type": "string"
            },
            "minItems": 1
        },
        "default_user_agent": {
            "description": "User agent used to send the GET request to download the feed. Overridden by individual feeds' 'user_agent' property. Defaults to the Python requests package user agent.",
            "type": "string"
        },
        "feeds": {
            "type": "object",
            "patternProperties": {
                ".+": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "description": "The URL of the RSS feed.",
                            "type": "string"
                        },
                        "user_agent": {
                            "description": "User agent used to send the GET request to download the feed. If missing, the global 'default_user_agent' is used.",
                            "type": "string"
                        },
                        "subscriptions": {
                            "type": "object",
                            "patternProperties": {
                                ".+": {
                                    "type": "object",
                                    "properties": {
                                        "pattern": {
                                            "description": "A regular expression to match the torrents you want from the RSS feed. Must have a group to capture the episode number, like so: (?P<episode>[0-9]{2}). The series number may also be captured with the 'series' group. Because the config is a JSON file, double backslashes are required.",
                                            "type": "string"
                                        },
                                        "episode_number": {
                                            "description": "The current episode number. This and 'series_number' below are updated automatically when the program downloads a new torrent for this subscription. Should be left alone unless for example you're adding a subscription and want it to start on episode 3.",
                                            "type": "integer"
                                        },
                                        "series_number": {
                                            "description": "The current series number. See 'episode_number', as the same applies here.",
                                            "type": "integer"
                                        },
                                        "command": {
                                            "description": "Command to which the torrent URL is passed. '$URL' is substituted for the torrent's URL. This array is otherwise passed directly to Python's subprocess module, which takes care of any escaping or quoting, so there is no need to do so yourself. If missing, 'default_command' is used.",
                                            "type": "array",
                                            "items": {
                                                "type": "string"
                                            },
                                            "minItems": 1
                                        }
                                    },
                                    "required": [
                                        "pattern"
                                    ]
                                }
                            }
                        }
                    },
                    "required": [
                        "url",
                        "subscriptions"
                    ]
                }
            }
        }
    },
    "required": [
        "feeds"
    ]
}
