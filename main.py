﻿#!/usr/bin/env python
import csv
import gzip
import re
from argparse import ArgumentParser
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryFile

import boto3
import dateutil.parser


class ProgressLogger:
    prev_print_at = datetime.now()

    def log(self, prefix, count, total=None):
        if self.prev_print_at > datetime.now() - timedelta(seconds=1):
            return
        print(
            "{}... {:03.2f}{}".format(
                prefix,
                count * 100 / total if total else count,
                '%' if total else ''
            ), end='\r'
        )
        self.prev_print_at = datetime.now()


progress_logger = ProgressLogger()


class LogDownloader:
    folder = Path("./download")

    def __init__(self, start, end, external=False, internal=False, force_download=False):
        self.start, self.end = start, end
        self.external, self.internal = external, internal
        self.force_download = force_download

    def download(self):
        self.folder.mkdir(exist_ok=True)

        date = self.start.date()
        while date <= self.end.date():
            self._download_with_date(date)
            date = date + timedelta(days=1)

    def _download_with_date(self, date):
        print("Start downloading files on {}.".format(date))
        base_prefix = 'AWSLogs/710026814108/elasticloadbalancing/ap-northeast-1/{}/{:02d}/{:02d}/'.format(
            date.year, date.month, date.day
        )
        external_prefix = '710026814108_elasticloadbalancing_ap-northeast-1_app.api-prod-elb.'
        internal_prefix = '710026814108_elasticloadbalancing_ap-northeast-1_app.api-prod-internal-elb.'

        if self.external:
            self._download_with_prefix(base_prefix + external_prefix)

        if self.internal:
            self._download_with_prefix(base_prefix + internal_prefix)

    def _download_with_prefix(self, prefix):
        bucket = 'prod-lbs-access-log'
        s3_client = boto3.client('s3')

        ret = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix
        )

        if 'Contents' not in ret:
            raise RuntimeError("no files be found on S3")

        keys = [content['Key'] for content in ret['Contents']]
        keys = self._filter_object_keys(keys, prefix)
        if not keys:
            raise RuntimeError("No objects matched given time period.")

        count = 0
        total = len(keys)
        exist = 0
        for key in keys:
            file_name = key.strip(prefix)

            if not self.force_download and (self.folder / file_name).exists():
                total -= 1
                exist += 1
                continue

            boto3.resource('s3').Object(bucket, key).download_file(
                str(self.folder / file_name)
            )
            count += 1
            progress_logger.log('Download', count, total)

        results = "Download complete!"
        if exist:
            results += " Skip {} existed files.".format(exist)
        print(results + " " * 10)

    def _filter_object_keys(self, keys, prefix):
        def is_valid(key):
            key = key.strip(prefix)
            obj_datetime = datetime.strptime(key.split("_")[1], "%Y%m%dT%H%MZ")
            return self.start < obj_datetime < self.end

        return list(filter(is_valid, keys))


class LogParser:
    def __init__(self, start, end, download_folder):
        self.start, self.end = start, end
        self.download_folder = download_folder
        self.__temp_file = TemporaryFile()

    @property
    def parsed_file(self):
        self.__temp_file.seek(0)
        return self.__temp_file

    def parse(self):
        logs = list(self.download_folder.glob('*.gz'))
        logs = self._filter(logs)
        total = len(logs)
        count = 0
        for l in logs:
            with gzip.open(l, 'rb') as in_f:
                for line in in_f:
                    text = line.decode()
                    split = text.split(" ")
                    text = "{datetime} {method} {url}\n".format(
                        datetime=split[1], method=split[12].strip("\""), url=split[13]
                    )
                    self.__temp_file.write(text.encode())
                count += 1
                progress_logger.log("Parsing gz files", count, total)
        self.__temp_file.seek(0)
        print("Parsing gz files complete!" + " " * 10)

    def _filter(self, logs):
        def is_valid(filepath):
            f_datetime = datetime.strptime(filepath.name.split("_")[1], "%Y%m%dT%H%MZ")
            return self.start < f_datetime < self.end

        return list(filter(is_valid, logs))


class LogAnalyzer:
    def __init__(self, start, end):
        self.start, self.end = start, end

        self.stats_file = Path('./out/stats_{}_{}.csv'.format(start.isoformat(), end.isoformat()))
        self.stats_file.parent.mkdir(parents=True, exist_ok=True)

        self.normalize_handler = {
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/\d*/achievements'):
                "https://api.soocii.me:443/graph/v1.2/<id>/achievements",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/\d*/followees/count'):
                "https://api.soocii.me:443/graph/v1.2/<id>/followees/count",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/\d*/followers/count'):
                "https://api.soocii.me:443/graph/v1.2/<id>/followers/count",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/users/\d*/followees'):
                "https://api.soocii.me:443/graph/v1.2/users/<id>/followees",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/users/\d*/followers'):
                "https://api.soocii.me:443/graph/v1.2/users/<id>/followers",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/\d*/friendship'):
                "https://api.soocii.me:443/graph/v1.2/<id>/friendship",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/\d*/posts'):
                "https://api.soocii.me:443/graph/v1.2/<id>/posts",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/me/feed/\w*-\w*'):
                "https://api.soocii.me:443/graph/v1.2/me/feed/<status_id>",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/\w*-\w*/comments'):
                "https://api.soocii.me:443/graph/v1.2/<status_id>/comments",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/posts/\w*-\w*/likes'):
                "https://api.soocii.me:443/graph/v1.2/posts/<status_id>/likes",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/\d*/pinned-posts'):
                "https://api.soocii.me:443/graph/v1.2/<id>/pinned-posts",
            re.compile(r'https://api\.soocii\.me:443/graph/v1\.2/me/posts/\w*-\w'):
                "https://api.soocii.me:443/graph/v1.2/me/posts/<status_id>"
        }

    def stat_api_calls(self, parsed_file):
        stats = defaultdict(lambda: 0)
        total = sum(1 for _ in parsed_file)
        parsed_file.seek(0)
        count = 0
        for line in parsed_file:
            count += 1
            progress_logger.log("Analyzing", count, total)
            line = line.decode()
            line = line.strip('\n')
            log_at, method, url = self._parse_line(line)

            if not (self.start < log_at < self.end):
                continue
            if 'content/corpus' in url:
                continue

            service = self._identify_service(url)
            url = self._normalize_url(url)
            stats["{} {} {}".format(service, method, url)] += 1

        with self.stats_file.open('w') as out_f:
            writer = csv.writer(out_f)
            writer.writerow(['service', 'method', 'url', 'count'])
            for key, count in stats.items():
                split = key.split(' ')
                service, method, url = split[0], split[1], split[2]
                writer.writerow([service, method, url, count])

        print("Analyzing logs complete!" + " " * 12)

    def _parse_line(self, line):
        log_datetime, method, url = line.split(' ')
        log_datetime = datetime.strptime(
            log_datetime, '%Y-%m-%dT%H:%M:%S.%fZ'
        )
        return log_datetime, method, url

    def _identify_service(self, url):
        service = ''
        if 'api/' in url:
            service = 'jarvis'
        if 'graph/v' in url:
            service = 'pepper'
        if 'recommendation/v' in url:
            service = 'vision'
        if 'search' in url:
            service = 'pym'
        if 'titan' in url:
            service = 'titan'
        return service

    def _normalize_url(self, url):
        url = url.split('?')[0]
        url = url.rstrip('/')
        for ptn, endpoint in self.normalize_handler.items():
            if ptn.match(url):
                url = endpoint
        return url


def setup_args_parser():
    def convert_datetime_str(d_str):
        dt = dateutil.parser.parse(d_str)
        dt = dt.astimezone(timezone.utc)
        dt = dt.replace(tzinfo=None)
        return dt

    parser = ArgumentParser(description="Analyze ALB logs by datetime duration")
    parser.add_argument('start', type=convert_datetime_str, help="Start datetime")
    parser.add_argument('end', type=convert_datetime_str, help="End datetime")
    parser.add_argument("-e", "--external", action="store_true", dest="ext", default=True,
                        help="Analyze external ALB (default on)")
    parser.add_argument("-i", "--internal", action="store_true", dest="int", default=False,
                        help="Analyze internal ALB (default off)")
    parser.add_argument("--force-download", action="store_true", dest="force_download", default=False,
                        help="Download files from S3 even file exists locally.")
    return parser


if __name__ == '__main__':
    arg_parser = setup_args_parser()
    args = arg_parser.parse_args()

    analyzer = LogAnalyzer(args.start, args.end)
    if analyzer.stats_file.exists():
        cont = input("{} exist. Do you want to continue? [Y|n]".format(analyzer.stats_file))
        if cont.lower() == 'n':
            exit()

    downloader = LogDownloader(args.start, args.end, args.ext, args.int, args.force_download)
    if args.force_download:
        print("Force download on. Overwriting existed files in {} folder.".format(downloader.folder))
    downloader.download()

    log_parser = LogParser(args.start, args.end, downloader.folder)
    log_parser.parse()

    analyzer.stat_api_calls(log_parser.parsed_file)
