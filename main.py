﻿#!/usr/bin/env python
import csv
import gzip
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import boto3


def print_progress(prefix, count, total=None):
    print(
        "{}... {:03.2f}{}".format(
            prefix,
            count * 100 / total if total else count,
            '%' if total else ''
        ), end='\r'
    )


download_folder = Path("./download")


class LogDownloader:
    def download(self, date, external=False, internal=False):
        if not download_folder.exists():
            download_folder.mkdir()

        base_prefix = 'AWSLogs/710026814108/elasticloadbalancing/ap-northeast-1/{}/{:02d}/{:02d}/'.format(
            date.year, date.month, date.day
        )
        external_prefix = '710026814108_elasticloadbalancing_ap-northeast-1_app.api-prod-elb.'
        internal_prefix = '710026814108_elasticloadbalancing_ap-northeast-1_app.api-prod-internal-elb.'

        if external:
            self._download_with_prefix(
                base_prefix + external_prefix, 'external')

        if internal:
            self._download_with_prefix(
                base_prefix + internal_prefix, 'internal')

    def _download_with_prefix(self, prefix, source):
        bucket = 'prod-lbs-access-log'
        s3_client = boto3.client('s3')

        ret = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix
        )
        print("{} have {} objects".format(source, ret['KeyCount']))
        if 'Contents' not in ret:
            raise RuntimeError("no files be found on S3")

        count = 0
        total = len(ret['Contents'])
        for key in (content['Key'] for content in ret['Contents']):
            file_name = key.replace(prefix, '')
            if (download_folder / file_name).exists():
                total -= 1
                continue
            boto3.resource('s3').Object(bucket, key).download_file(
                str(download_folder / file_name)
            )
            count += 1
            print_progress('Download', count, total)
        print("Download complete!" + " " * 10)


merged_file = Path('./merged')


def merge_logs():
    if merged_file.exists():
        print("{} exists.".format(merged_file))
        return

    logs = list(download_folder.glob('*.gz'))
    total = len(logs)
    count = 0
    for p in logs:
        with gzip.open(p, 'rb') as in_f, merged_file.open('wb') as out_f:
            out_f.write(in_f.read())
            count += 1
            print_progress('Decompression', count, total)
    print("Decompression complete!" + " " * 10)


parsed_file = Path('./parsed')


def parse_logs():
    count = 0
    with merged_file.open('r') as in_f, parsed_file.open('w') as out_f:
        for line in in_f:
            split = line.split(' ')
            out_f.write("{datetime} {method} {url}\n".format(
                datetime=split[1], method=split[12][1:], url=split[13])
            )
            count += 1
            print_progress('Parseing', count)
    print("Parse complete!" + " " * 10)


class LogAnalyzer:
    def __init__(self):
        self.stat_file = Path('./stat.csv')
        self.count = 0

    def stat_api_calls(self, start, end):
        stats = defaultdict(lambda: 0)
        with parsed_file.open('r') as in_f:
            for line in in_f:
                self.count += 1
                print_progress("Analyzing", self.count)
                line = line.replace('\n', '')
                log_at, method, url = self._parse_line(line)

                if log_at < start or log_at > end:
                    continue
                if 'content/corpus' in url:
                    continue

                service = self._identify_service(url)
                url = self._normalize_url(url)
                stats["{} {} {}".format(service, method, url)] += 1

        print(stats)

        with self.stat_file.open('w') as out_f:
            writer = csv.writer(out_f)
            for key, count in stats.items():
                split = key.split(' ')
                service, method, url = split[0], split[1], split[2]
                writer.writerow([service, method, url, count])

    def _parse_line(self, line):
        log_datetime, method, url = line.split(' ')
        log_datetime = datetime.strptime(
            log_datetime, '%Y-%m-%dT%H:%M:%S.%fZ'
        )
        return log_datetime, method, url

    def _identify_service(self, url):
        service = ''
        if 'api/v1' in url:
            service = 'jarvis'
        if 'api/getG' in url:
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
        return url.split('?')[0]


if __name__ == '__main__':
    LogDownloader().download(date(2018, 3, 21), external=True)
    merge_logs()
    parse_logs()
    LogAnalyzer().stat_api_calls(datetime(2018, 3, 21, 0), datetime(2018, 3, 21, 23))

    # parser = optparse.OptionParser()
    # parser.add_option('-f', '--file',
    #                   dest='file_name',
    #                   default='',
    #                   help='Give the time(Mins) for check')
    # options, remainder = parser.parse_args()
    # if options.file_name:
    #     la = LogAnalysis(options.file_name)
    #     la.start()
