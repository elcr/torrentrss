from __future__ import annotations

import os
import re
import sys
import json
import shutil
import hashlib
import logging
import tempfile
import subprocess
from pathlib import Path
from typing.re import Pattern, Match
from typing import (
    Any,
    Dict,
    List,
    Tuple,
    Union,
    TextIO,
    Iterator,
    Optional,
    cast
)

import click
import requests
import feedparser
import jsonschema
import pkg_resources
from feedparser import FeedParserDict

NAME = 'torrentrss'
VERSION = '0.7'
WINDOWS = os.name == 'nt'
CONFIG_DIRECTORY = Path(click.get_app_dir(NAME))
CONFIG_PATH = Path(CONFIG_DIRECTORY, 'config.json')
CONFIG_SCHEMA_FILENAME = 'config_schema.json'
LOG_MESSAGE_FORMAT = '[%(asctime)s %(levelname)s] %(message)s'
TEMPORARY_DIRECTORY = Path(tempfile.gettempdir())
COMMAND_PATH_ARGUMENT = '$PATH_OR_URL'
TORRENT_MIMETYPE = 'application/x-bittorrent'


Json = Dict[str, Any]
PathOrUrl = Union[Path, str]


class TorrentRSSError(Exception):
    pass


class ConfigError(TorrentRSSError):
    pass


class FeedError(TorrentRSSError):
    pass


def get_schema() -> str:
    return pkg_resources.resource_string(__name__, CONFIG_SCHEMA_FILENAME) \
        .decode('utf-8')


def get_schema_dict() -> Json:
    return json.loads(get_schema())


def show_exception_notification(exception: Exception) -> None:
    text = f'An exception of type {exception.__class__.__name__} occurred.'
    if shutil.which('notify-send') is not None:
        subprocess.Popen(['notify-send', '--app-name', NAME, NAME, text])


class TorrentRSS:
    _json: Json

    path: Path
    feeds: Dict[str, Feed]
    default_directory: Path
    default_command: Command
    default_user_agent: Optional[str]
    replace_windows_forbidden_characters: bool

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        with open(self.path, encoding='utf-8') as file:
            self._json = json.load(file)
        jsonschema.validate(self._json, get_schema_dict())

        self.default_directory = Path(
            self._json.get('default_directory', TEMPORARY_DIRECTORY)
        )
        self.default_command = Command(
            self._json.get('default_command'),
            self._json.get('default_command_shell_enabled', False)
        )
        self.default_user_agent = self._json.get('default_user_agent')
        self.replace_windows_forbidden_characters = self._json.get(
            'replace_windows_forbidden_characters',
            WINDOWS
        )
        self.feeds = {
            name: Feed(config=self, name=name, **feed_dict)
            for name, feed_dict in self._json['feeds'].items()
        }

    def check_feeds(self) -> None:
        for feed in self.feeds.values():
            for sub, entry in feed.matching_subs():
                path_or_url = feed.download_entry(entry, sub.directory)
                sub.command(path_or_url)

    def save_episode_numbers(self, file: Optional[TextIO] = None) -> None:
        logging.info('Writing episode numbers')
        json_feeds = self._json['feeds']
        for feed_name, feed in self.feeds.items():
            json_subs = json_feeds[feed_name]['subscriptions']
            for sub_name, sub in feed.subscriptions.items():
                sub_dict = json_subs[sub_name]
                if sub.number.series is not None:
                    sub_dict['series_number'] = sub.number.series
                if sub.number.episode is not None:
                    sub_dict['episode_number'] = sub.number.episode
        text = json.dumps(self._json, indent=4)
        if file is None:
            with open(self.path, mode='w', encoding='utf-8') as file:
                file.write(text)
        else:
            file.write(text)


class Command:
    arguments: Optional[List[str]]
    shell: bool

    def __init__(
        self,
        arguments: Optional[List[str]] = None,
        shell: bool = False
    ) -> None:
        self.arguments = arguments
        self.shell = shell

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(arguments={self.arguments})'

    def subbed_arguments(self, path_or_url: PathOrUrl) -> Iterator[str]:
        # The repl parameter here is a function which at first looks like it
        # could just be a string, but it actually needs to be a function or
        # else escapes in the string would be processed, leading to problems
        # when dealing with file paths, for example.
        # See: https://docs.python.org/3/library/re.html#re.sub
        #      https://stackoverflow.com/a/16291763/3289208
        def replacer(_: Match) -> str:
            return os.fspath(path_or_url)
        for argument in cast(List[str], self.arguments):
            yield re.sub(
                pattern=re.escape(COMMAND_PATH_ARGUMENT),
                repl=replacer,
                string=argument
            )

    @staticmethod
    def startfile(path_or_url: PathOrUrl) -> None:
        # click.launch uses os.system on Windows, which shows a cmd.exe
        # window for a split second. Hence os.startfile is preferred.
        if WINDOWS:
            os.startfile(path_or_url)
            return
        click.launch(os.fspath(path_or_url))

    def __call__(self, path_or_url: PathOrUrl) -> Optional[subprocess.Popen]:
        if self.arguments is not None:
            startupinfo: Optional[subprocess.STARTUPINFO]
            if WINDOWS:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
            else:
                startupinfo = None
            arguments = list(self.subbed_arguments(path_or_url))
            logging.info(f'Launching subprocess with arguments {arguments}')
            return subprocess.Popen(
                args=arguments,
                shell=self.shell,
                startupinfo=startupinfo
            )
        logging.info(f'Launching {path_or_url!r} with default program')
        self.startfile(path_or_url)
        return None


class Feed:
    _user_agent: Optional[str]

    config: TorrentRSS
    subscriptions: Dict[str, 'Subscription']
    name: str
    url: str
    prefer_torrent_url: bool
    hide_torrent_filename: bool

    def __init__(
        self, *,
        config: TorrentRSS,
        name: str,
        url: str,
        subscriptions: Json,
        user_agent: Optional[str] = None,
        prefer_torrent_url: bool = True,
        hide_torrent_filename: bool = True
    ) -> None:
        self._user_agent = None

        self.config = config
        self.name = name
        self.url = url
        self.subscriptions = {
            name: Subscription(feed=self, name=name, **sub_dict)
            for name, sub_dict in subscriptions.items()
        }
        self.user_agent = user_agent
        self.prefer_torrent_url = prefer_torrent_url
        self.hide_torrent_filename = hide_torrent_filename

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(name={self.name!r}, url={self.url!r})'

    @property
    def user_agent(self) -> Optional[str]:
        return self._user_agent or self.config.default_user_agent

    @user_agent.setter
    def user_agent(self, value: Optional[str]) -> None:
        self._user_agent = value

    @property
    def headers(self) -> Dict[str, str]:
        if self.user_agent is None:
            return {}
        return {'User-Agent': self.user_agent}

    def fetch(self) -> FeedParserDict:
        rss = feedparser.parse(self.url, request_headers=self.headers)
        if rss['bozo']:
            raise FeedError(
                f'Feed {self.name!r}: error parsing url {self.url!r}'
            ) from rss['bozo_exception']
        logging.info(f'Feed {self.name!r}: downloaded url {self.url!r}')
        return rss

    def matching_subs(self) -> Iterator[Tuple[Subscription, FeedParserDict]]:
        if not self.subscriptions:
            return

        rss = self.fetch()
        # episode numbers are compared against subscriptions' numbers as they
        # were at the beginning of the method rather than comparing to the most
        # recent match. this ensures that all matches in the feed are yielded
        # regardless of whether they are in numeric order.
        original_numbers = {
            sub: sub.number for sub in
            self.subscriptions.values()
        }

        for index, entry in enumerate(reversed(rss['entries'])):
            index = len(rss['entries']) - index - 1
            for sub in self.subscriptions.values():
                match = sub.regex.search(entry['title'])
                if match:
                    number = EpisodeNumber.from_regex_match(match)
                    if number > original_numbers[sub]:
                        logging.info(
                            f'MATCH: entry {index} {entry["title"]!r} has '
                            + f'greater number than sub {sub.name!r}: '
                            + f'{number} > {original_numbers[sub]}'
                        )
                        sub.number = number
                        yield sub, entry
                    else:
                        logging.debug(
                            f'NO MATCH: entry {index} {entry["title"]!r} '
                            + 'matches but number less than or equal to sub '
                            + f'{sub.name!r}: {number} <= '
                            + f'{original_numbers[sub]}'
                        )
                else:
                    logging.debug(
                        f'NO MATCH: entry {index} {entry["title"]!r} against '
                        + f'sub {sub.name!r}'
                    )

    @staticmethod
    def torrent_url_for_entry(rss_entry: FeedParserDict) -> str:
        for link in rss_entry['links']:
            if link['type'] == TORRENT_MIMETYPE:
                href = link['href']
                logging.debug(
                    f'Entry {rss_entry["title"]!r}: first link with mimetype '
                    + f'{TORRENT_MIMETYPE!r} is {href!r}'
                )
                return href
        link = rss_entry['link']
        logging.info(
            f'Entry {rss_entry["title"]!r}: no link with mimetype '
            + f'{TORRENT_MIMETYPE!r}, returning first link {link!r}'
        )
        return link

    def download_entry_torrent_file(
        self,
        url: str,
        title: str,
        directory: Path
    ) -> Path:
        logging.debug(
            f'Feed {self.name!r}: sending GET request to {url!r} '
            + f'with headers {self.headers}'
        )
        response = requests.get(url, headers=self.headers)
        logging.debug(
            f'Feed {self.name!r}: response status code is '
            + f"{response.status_code}, 'ok' is {response.ok}"
        )
        response.raise_for_status()

        if self.hide_torrent_filename:
            title = hashlib.sha256(response.content).hexdigest()
        elif self.config.replace_windows_forbidden_characters:
            title = re.sub(pattern=r'[\\/:\*\?"<>\|]', repl='_', string=title)
        path = Path(directory, title + '.torrent')

        directory.mkdir(parents=True, exist_ok=True)
        logging.debug(
            f'Feed {self.name!r}: writing response bytes to file {path}'
        )
        path.write_bytes(response.content)
        return path

    def download_entry(
        self,
        rss_entry: FeedParserDict,
        directory: Path
    ) -> PathOrUrl:
        try:
            url = self.torrent_url_for_entry(rss_entry)
        except Exception as error:
            raise FeedError(
                f'Feed {self.name!r}: failed to get url for entry '
                + f'{rss_entry["title"]!r}'
            ) from error
        if self.prefer_torrent_url:
            logging.debug(f'Feed {self.name!r}: returning torrent url {url!r}')
            return url

        try:
            return self.download_entry_torrent_file(
                url=url,
                title=rss_entry['title'],
                directory=directory
            )
        except Exception as error:
            raise FeedError(
                f'Feed {self.name!r}: failed to download {url}'
            ) from error


class EpisodeNumber:
    series: Optional[int]
    episode: Optional[int]

    def __init__(self, series: Optional[int], episode: Optional[int]) -> None:
        self.series = series
        self.episode = episode

    @classmethod
    def from_regex_match(cls, match: Match) -> EpisodeNumber:
        groups = match.groupdict()
        return cls(
            series=int(groups['series']) if 'series' in groups else None,
            episode=int(groups['episode'])
        )

    def __gt__(self, other: EpisodeNumber) -> bool:
        if self.episode is None:
            return False
        if other.episode is None:
            return True
        if self.series is not None \
                and other.series is not None \
                and self.series != other.series:
            return self.series > other.series
        return self.episode > other.episode

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(series={self.series}, episode={self.episode})'

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, EpisodeNumber):
            return NotImplemented
        return self.series == other.series and self.episode == other.episode


class Subscription:
    feed: Feed
    name: str
    regex: Pattern
    number: EpisodeNumber
    directory: Path
    command: Command

    def __init__(
        self,
        feed: Feed,
        name: str,
        pattern: str,
        series_number: Optional[int] = None,
        episode_number: Optional[int] = None,
        directory: Optional[str] = None,
        command: Optional[List[str]] = None,
        use_shell_for_command: bool = False
    ) -> None:
        self.feed = feed
        self.name = name
        try:
            self.regex = re.compile(pattern)
        except re.error as error:
            args = ", ".join(error.args)
            raise ConfigError(
                f'Feed {feed.name!r} sub {name!r} pattern '
                f'{pattern!r} not valid regex: {args}'
            ) from error
        if 'episode' not in self.regex.groupindex:
            raise ConfigError(
                f'Feed {feed.name!r} sub {name!r} pattern '
                f'{pattern!r} has no group for the episode number'
            )
        self.number = EpisodeNumber(series=series_number,
                                    episode=episode_number)
        self.directory = (
            feed.config.default_directory
            if directory is None else
            Path(directory)
        )
        self.command = (
            feed.config.default_command
            if command is None else
            Command(command, use_shell_for_command)
        )

    def __repr__(self):
        return f'{self.__class__.__name__}name={self.name!r}, feed={self.feed.name!r})'


def configure_logging(level: str) -> None:
    logging.basicConfig(
        format=LOG_MESSAGE_FORMAT,
        level=level,
        stream=sys.stdout
    )

    # silence requests' logging in all but the worst cases
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def print_schema(
    context: click.Context,
    parameter: click.Parameter,
    value: Any
) -> None:
    if value:
        print(get_schema())
        context.exit()


@click.command()
@click.option(
    '-l', '--logging-level',
    default='DEBUG',
    show_default=True,
    type=click.Choice(
        ['DISABLE', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    )
)
@click.option(
    '-p', '--print-config-schema',
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=print_schema
)
@click.help_option('-h', '--help')
@click.version_option(VERSION, '-v', '--version', message='%(version)s')
def main(logging_level: str) -> None:
    configure_logging(level=logging_level)

    try:
        try:
            config = TorrentRSS()
        except FileNotFoundError as error:
            raise click.Abort(
                f'No config file found at {str(CONFIG_PATH)!r}. '
                + "See '--print-config-schema' for reference."
            ) from error
        config.check_feeds()
        config.save_episode_numbers()
    except Exception as error:
        logging.exception(error.__class__.__name__)
        show_exception_notification(error)
        raise
    finally:
        logging.shutdown()
