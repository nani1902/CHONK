"""Ghostscript ``pdfwrite`` backend with a fixed argument set."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from chonk.models import CompressionError, Profile


def find_ghostscript(explicit_path: str | None) -> str:
    if explicit_path:
        executable = shutil.which(explicit_path) or (
            explicit_path if Path(explicit_path).is_file() else None
        )
        if not executable:
            raise CompressionError(f"Ghostscript executable not found: {explicit_path}")
        return str(executable)

    names = ("gs", "gswin64c.exe", "gswin32c.exe")
    for name in names:
        executable = shutil.which(name)
        if executable:
            return executable
    raise CompressionError(
        "Ghostscript was not found. Install Ghostscript and rerun, or pass its path "
        "with --ghostscript."
    )


def make_ghostscript_command(
    ghostscript: str,
    source: Path,
    output: Path,
    profile: Profile,
) -> list[str]:
    downsample = profile.dpi is not None
    args = [
        ghostscript,
        "-dSAFER",
        "-dBATCH",
        "-dNOPAUSE",
        "-dQUIET",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.7",
        "-dAutoRotatePages=/None",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-dSubsetFonts=true",
        "-dEmbedAllFonts=true",
        "-dPreserveAnnots=true",
        "-dPassThroughJPEGImages=true",
        "-dPassThroughJPXImages=true",
        f"-dDownsampleColorImages={str(downsample).lower()}",
        f"-dDownsampleGrayImages={str(downsample).lower()}",
        f"-dDownsampleMonoImages={str(downsample).lower()}",
        "-dColorImageDownsampleType=/Bicubic",
        "-dGrayImageDownsampleType=/Bicubic",
        "-dMonoImageDownsampleType=/Subsample",
    ]

    if downsample:
        assert profile.dpi is not None
        args.extend(
            [
                f"-dColorImageResolution={profile.dpi}",
                f"-dGrayImageResolution={profile.dpi}",
                f"-dMonoImageResolution={profile.dpi}",
                "-dColorImageDownsampleThreshold=1.0",
                "-dGrayImageDownsampleThreshold=1.0",
                "-dMonoImageDownsampleThreshold=1.0",
            ]
        )

    qfactor = f"{profile.qfactor:.2f}"
    # Both ACS and regular dictionaries are set: Ghostscript may select one
    # based on its automatic image-filter decision.
    image_params = (
        "<< /LockDistillerParams true "
        f"/ColorImageDict << /QFactor {qfactor} >> "
        f"/GrayImageDict << /QFactor {qfactor} >> "
        f"/ColorACSImageDict << /QFactor {qfactor} /Blend 1 /ColorTransform 1 "
        "/HSamples [1 1 1 1] /VSamples [1 1 1 1] >> "
        f"/GrayACSImageDict << /QFactor {qfactor} /Blend 1 /ColorTransform 1 "
        "/HSamples [1 1 1 1] /VSamples [1 1 1 1] >> "
        ">> setdistillerparams"
    )
    args.extend(["-sOutputFile=" + str(output), "-c", image_params, "-f", str(source)])
    return args


class GhostscriptBackend:
    """Runs a resolved Ghostscript executable. Callers choose the executable;
    arguments are fixed by :func:`make_ghostscript_command`."""

    def __init__(self, executable: str):
        self.executable = executable

    @classmethod
    def discover(cls, explicit_path: str | None = None) -> GhostscriptBackend:
        return cls(find_ghostscript(explicit_path))

    def compress(
        self, source: Path, output: Path, profile: Profile, *, timeout: int
    ) -> None:
        command = make_ghostscript_command(self.executable, source, output, profile)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CompressionError(
                f"Ghostscript exceeded the {timeout}-second timeout while trying "
                f"{profile.label}."
            ) from exc
        except OSError as exc:
            raise CompressionError(f"Could not start Ghostscript: {exc}") from exc

        if result.returncode != 0 or not output.is_file():
            details = (result.stderr or result.stdout or "No diagnostic was returned.").strip()
            details = details[-2_000:]
            raise CompressionError(
                f"Ghostscript could not compress the PDF using {profile.label}.\n{details}"
            )
