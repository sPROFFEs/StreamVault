"""FFmpeg arguments for explicitly enabled hardware video encoding."""


def hardware_h264(backend, device, *, downscale=False):
    scale = "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2," if downscale else ""
    if backend == "nvenc":
        return [], ["-vf", scale + "format=nv12", "-c:v", "h264_nvenc", "-preset", "p3",
                    "-tune", "ll", "-cq", "24", "-maxrate", "6M", "-bufsize", "6M"]
    if backend == "qsv":
        return ["-init_hw_device", f"vaapi=sv:{device}", "-init_hw_device", "qsv=qs@sv", "-filter_hw_device", "qs"], [
            "-vf", scale + "format=nv12,hwupload=extra_hw_frames=64", "-c:v", "h264_qsv",
            "-preset", "veryfast", "-global_quality", "24", "-maxrate", "6M", "-bufsize", "6M"]
    if backend == "vaapi":
        return ["-init_hw_device", f"vaapi=sv:{device}", "-filter_hw_device", "sv"], [
            "-vf", scale + "format=nv12,hwupload", "-c:v", "h264_vaapi", "-qp", "24", "-bf", "0"]
    return None
