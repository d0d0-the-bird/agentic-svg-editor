"""Inline data-URI SVG images as native nested SVG viewports for vector export."""
import argparse
import base64
import re
from pathlib import Path
from urllib.parse import unquote_to_bytes

from lxml import etree

SVG = 'http://www.w3.org/2000/svg'
XLINK = '{http://www.w3.org/1999/xlink}href'


def inline(root):
    count = 0
    while True:
        images = root.findall(f'.//{{{SVG}}}image')
        changed = False
        for image in images:
            href = image.get('href') or image.get(XLINK, '')
            if not href.startswith('data:image/svg+xml'):
                continue
            header, payload = href.split(',', 1)
            data = base64.b64decode(payload) if ';base64' in header else unquote_to_bytes(payload)
            source = etree.fromstring(data)
            count += 1
            prefix = f'inline-{count}-'
            used = {e.get('id') for e in root.iter() if e.get('id')}
            while any(prefix + e.get('id') in used for e in source.iter() if e.get('id')):
                prefix = 'nested-' + prefix
            ids = {e.get('id'): prefix + e.get('id') for e in source.iter() if e.get('id')}
            def references(value):
                value = re.sub(r'url\(\s*[\"\']?#([^\s)\"\']+)[\"\']?\s*\)',
                               lambda m: 'url(#' + ids.get(m[1], m[1]) + ')', value)
                return value
            for node in source.iter():
                for key, value in list(node.attrib.items()):
                    if key == 'id':
                        node.set(key, ids[value])
                    elif key in ('href', XLINK) and value.startswith('#'):
                        node.set(key, '#' + ids.get(value[1:], value[1:]))
                    else:
                        node.set(key, references(value))
                if node.tag == f'{{{SVG}}}style' and node.text:
                    node.text = references(node.text)
            if source.get('viewBox') is None:
                source.set('viewBox', f"0 0 {source.get('width')} {source.get('height')}")
            wrapper = etree.Element(f'{{{SVG}}}g')
            for key, value in image.attrib.items():
                if key not in ('href', XLINK, 'x', 'y', 'width', 'height', 'preserveAspectRatio'):
                    wrapper.set(key, value)
            for key in ('x', 'y', 'width', 'height', 'preserveAspectRatio'):
                if image.get(key) is not None:
                    source.set(key, image.get(key))
            source.set('overflow', 'hidden')
            wrapper.append(source)
            wrapper.tail = image.tail
            image.getparent().replace(image, wrapper)
            changed = True
        if not changed:
            return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    tree = etree.parse(str(args.input))
    count = inline(tree.getroot())
    tree.write(str(args.output), encoding='UTF-8', xml_declaration=True)
    print(f'Inlined {count} SVG images: {args.output}')


if __name__ == '__main__':
    main()
