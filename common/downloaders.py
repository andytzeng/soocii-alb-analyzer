from datetime import timedelta, datetime
from pathlib import Path

import boto3
import dateutil.parser

from common.loggers import ProgressLogger

progress_logger = ProgressLogger()


class LogDownloader:
    download_folder = Path('./download')
    ext_folder = download_folder / 'ext'
    int_folder = download_folder / 'int'

    def __init__(self, start, end, external, internal, force_download=False):
        self.start, self.end = start, end
        self.external, self.internal = external, internal
        self.force_download = force_download

    def download(self):
        self.download_folder.mkdir(exist_ok=True)
        self.ext_folder.mkdir(exist_ok=True)
        self.int_folder.mkdir(exist_ok=True)

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
            print("Start download external ALB logs")
            self._download_with_prefix(base_prefix + external_prefix, self.ext_folder)

        if self.internal:
            print("Start download internal ALB logs")
            self._download_with_prefix(base_prefix + internal_prefix, self.int_folder)

    def _download_with_prefix(self, prefix, to_folder):
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

            if not self.force_download and (to_folder / file_name).exists():
                total -= 1
                exist += 1
                continue

            boto3.resource('s3').Object(bucket, key).download_file(
                str(to_folder / file_name)
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


class DownloadFilePeriodFilter:
    def __init__(self, start, end, external, internal):
        self.start, self.end = start, end
        self.ext, self.int = external, internal

        self.files = []
        if self.ext:
            all_files = LogDownloader.ext_folder.glob('*.gz')
            self.files += list(filter(self.__is_in_period, all_files))

        if self.int:
            all_files = LogDownloader.int_folder.glob('*.gz')
            self.files += list(filter(self.__is_in_period, all_files))

    def __is_in_period(self, file):
        dt = str(file).split('_')[1]
        dt = dateutil.parser.parse(dt)
        dt = dt.replace(tzinfo=None)
        return self.start < dt < self.end
