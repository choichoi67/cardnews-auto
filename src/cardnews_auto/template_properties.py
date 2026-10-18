"""Read editable properties actually present in the imported PSD."""
from psd_tools.constants import Tag


def rgb(desc):
    return '#%02x%02x%02x' % tuple(round(float(desc[k])) for k in (b'Rd  ', b'Grn ', b'Bl  '))


def properties(layer):
    if layer.kind == 'gradientfill':
        desc = layer.tagged_blocks.get_data(Tag.GRADIENT_FILL_SETTING)
        grad = desc[b'Grad']
        if desc[b'Type'].enum != b'Lnr ':
            return {}
        mask = layer.mask.topil() if layer.has_mask() else None
        if mask is not None:
            return {}  # Nonempty masks require a separate mask editor.
        return {'gradient': {
            'angle': float(desc.get(b'Angl', 0)), 'scale': float(desc.get(b'Scl ', 100)),
            'offset_x': float(desc.get(b'Ofst', {}).get(b'Hrzn', 0)),
            'offset_y': float(desc.get(b'Ofst', {}).get(b'Vrtc', 0)),
            'reverse': bool(desc.get(b'Rvrs', False)),
            'colors': [{'position': float(s[b'Lctn']) / 4096, 'color': rgb(s[b'Clr ']),
                        'midpoint': float(s[b'Mdpn']) / 100} for s in grad[b'Clrs']],
            'alphas': [{'position': float(s[b'Lctn']) / 4096, 'opacity': float(s[b'Opct']) / 100,
                        'midpoint': float(s[b'Mdpn']) / 100} for s in grad[b'Trns']],
        }}
    if layer.kind == 'shape':
        origin = layer.tagged_blocks.get_data(Tag.VECTOR_ORIGINATION_DATA)
        items = origin.get(b'keyDescriptorList', []) if origin else []
        if len(items) != 1 or int(items[0].get(b'keyOriginType', 0)) != 1:
            return {}
        stroke = layer.tagged_blocks.get_data(Tag.VECTOR_STROKE_DATA)
        fill = layer.tagged_blocks.get_data(Tag.VECTOR_STROKE_CONTENT_DATA)
        if fill and b'Clr ' in fill and stroke:
            return {'shape': {'fill': rgb(fill[b'Clr ']),
                    'stroke': rgb(stroke[b'strokeStyleContent'][b'Clr ']),
                    'stroke_width': float(stroke[b'strokeStyleLineWidth']),
                    'fill_enabled': bool(stroke.get(b'fillEnabled', True)),
                    'stroke_enabled': bool(stroke.get(b'strokeEnabled', True))}}
    return {}
