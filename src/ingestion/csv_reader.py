import codecs
import csv
from dataclasses import dataclass
from pathlib import Path

from src.exceptions import FormatDetectionError, IngestionError


@dataclass(frozen=True)
class CsvSettings:
    """Describe the settings used to read a CSV file."""

    encoding: str
    delimiter: str
    encoding_detected: bool
    delimiter_detected: bool


class CsvReader:
    """Inspect CSV files before loading their contents."""

    SUPPORTED_DELIMITERS = (",", ";", "\t", "|")
    SAMPLE_SIZE = 64 * 1024

    def inspect(
        self,
        path: Path,
        encoding: str | None = None,
        delimiter: str | None = None,
    ) -> CsvSettings:
        """Inspect a local CSV, optionally using explicit settings."""
        if delimiter is not None and (
            len(delimiter) != 1 or delimiter in ("\r", "\n", '"', "\0")
        ):
            raise IngestionError("The delimiter must be one valid character.")

        sample = self._read_sample(path)
        selected_encoding = encoding if encoding is not None else self._detect_encoding(sample)
        text = self._decode_sample(sample, selected_encoding)
        if not text.strip():
            raise IngestionError("The CSV file does not contain readable content.")

        selected_delimiter = delimiter if delimiter is not None else self._detect_delimiter(text)
        return CsvSettings(
            encoding=selected_encoding,
            delimiter=selected_delimiter,
            encoding_detected=encoding is None,
            delimiter_detected=delimiter is None,
        )

    def _read_sample(self, path: Path) -> bytes:
        """Read a bounded sample without loading the entire file."""
        try:
            with path.open("rb") as file:
                sample = file.read(self.SAMPLE_SIZE)
        except OSError as error:
            raise IngestionError(f"Unable to open CSV file: {path.name}.") from error

        if not sample:
            raise IngestionError("The CSV file is empty.")
        return sample

    def _detect_encoding(self, sample: bytes) -> str:
        """Recognize Unicode signatures or validate UTF-8."""
        signatures = (
            (codecs.BOM_UTF32_LE, "utf-32"),
            (codecs.BOM_UTF32_BE, "utf-32"),
            (codecs.BOM_UTF8, "utf-8-sig"),
            (codecs.BOM_UTF16_LE, "utf-16"),
            (codecs.BOM_UTF16_BE, "utf-16"),
        )
        for signature, encoding in signatures:
            if sample.startswith(signature):
                return encoding

        try:
            self._decode_sample(sample, "utf-8")
        except IngestionError as error:
            raise FormatDetectionError(
                "Unable to identify the encoding. Select it manually."
            ) from error

        if b"\0" in sample:
            raise FormatDetectionError(
                "Unexpected null bytes. Select the encoding manually."
            )
        return "utf-8"

    @staticmethod
    def _decode_sample(sample: bytes, encoding: str) -> str:
        """Decode strictly while allowing a partial final character."""
        try:
            decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
            return decoder.decode(sample, final=False)
        except (LookupError, UnicodeError) as error:
            raise IngestionError(
                f"Unable to decode the CSV sample using '{encoding}'."
            ) from error

    def _detect_delimiter(self, text: str) -> str:
        """Suggest a separator using Python's CSV dialect detector."""
        try:
            dialect = csv.Sniffer().sniff(
                text, delimiters="".join(self.SUPPORTED_DELIMITERS)
            )
        except csv.Error as error:
            raise FormatDetectionError(
                "Unable to identify the delimiter. Select it manually."
            ) from error
        return dialect.delimiter