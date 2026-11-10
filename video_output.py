"""Resolve video aspect ratio without depending on canvas node IDs."""
def video_dimensions(settings, references):
    width, height = int(settings.get('width',1024)), int(settings.get('height',576))
    if settings.get('ratio') != 'source':
        return width, height
    source = references.get('first') or references.get('last') or {}
    sw, sh = source.get('width',16), source.get('height',9)
    if not sw or not sh or sw <= 0 or sh <= 0:
        sw, sh = 16, 9
    value = settings.get('resolution','1024')
    long = max(width,height) if value == 'custom' else int(value)
    if long not in (512,768,1024,1536):
        raise ValueError('不支持的视频清晰度')
    align = lambda value: max(256,min(1536,int(value/32+0.5)*32))
    return align(long*sw/max(sw,sh)), align(long*sh/max(sw,sh))
