"""Bounded read-only extraction of cell text and links from XLSX files."""
import io
import pathlib
import posixpath
import xml.etree.ElementTree as ET
import zipfile


MAX_EXPANDED = 64 * 1024 * 1024
MAX_XML = 12 * 1024 * 1024
WORKBOOK = 'xl/workbook.xml'
WORKBOOK_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'


def _xml(archive, part):
    info = archive.getinfo(part)
    if info.file_size > MAX_XML:
        raise ValueError('엑셀 XML 크기가 읽기 제한을 넘습니다.')
    data = archive.read(info)
    declarations = data.replace(b'\0', b'').upper()
    if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
        raise ValueError('외부 선언이 포함된 엑셀 XML은 읽을 수 없습니다.')
    return ET.fromstring(data)


def _relationships(archive, part):
    folder, name = posixpath.split(part)
    rels = posixpath.join(folder, '_rels', name + '.rels')
    if rels not in archive.namelist():
        return {}
    return {item.get('Id'): item.attrib for item in _xml(archive, rels).findall('{*}Relationship')}


def _internal_target(part, relationship):
    target = relationship['Target']
    if relationship.get('TargetMode') == 'External' or '\\' in target or ':' in target:
        raise ValueError('엑셀 내부 시트 경로가 올바르지 않습니다.')
    path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(part), target))
    if path.startswith('../') or not path.startswith('xl/'):
        raise ValueError('엑셀 내부 시트 경로가 올바르지 않습니다.')
    return path


def _id(item):
    return next((value for key, value in item.attrib.items() if key.endswith('}id')), None)


def _workbook(archive):
    infos = archive.infolist()
    if len(infos) > 3000 or sum(item.file_size for item in infos) > MAX_EXPANDED:
        raise ValueError('엑셀 내부 데이터가 읽기 제한을 넘습니다.')
    names = set()
    for item in infos:
        name = item.filename.replace('\\', '/')
        if (name in names or name.startswith('/') or '..' in pathlib.PurePosixPath(name).parts
                or ':' in name or item.flag_bits & 1):
            raise ValueError('엑셀 내부 경로 또는 암호화 상태를 확인하세요.')
        names.add(name)
    types = _xml(archive, '[Content_Types].xml')
    if not any(item.get('PartName') == '/' + WORKBOOK and item.get('ContentType') == WORKBOOK_TYPE
               for item in types.findall('{*}Override')):
        raise ValueError('표준 XLSX 통합문서 형식이 아닙니다.')
    workbook = _xml(archive, WORKBOOK)
    return workbook.findall('{*}sheets/{*}sheet'), _relationships(archive, WORKBOOK)


def inspect(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            sheets, relationships = _workbook(archive)
            worksheets = 0
            for sheet in sheets:
                relationship = relationships.get(_id(sheet), {})
                if relationship.get('Type', '').endswith('/worksheet'):
                    archive.getinfo(_internal_target(WORKBOOK, relationship))
                    worksheets += 1
            if not worksheets:
                raise ValueError('XLSX에 읽을 수 있는 워크시트가 없습니다.')
            return {'worksheets': worksheets}
    except (KeyError, zipfile.BadZipFile, ET.ParseError) as error:
        raise ValueError('XLSX 통합문서 형식을 확인할 수 없습니다.') from error


def _rich_text(element):
    return ''.join(node.text or '' for node in (*element.findall('{*}t'), *element.findall('{*}r/{*}t')))


def _cell_text(cell, strings):
    formula = cell.find('{*}f')
    if formula is not None and formula.text:
        return '=' + formula.text
    kind = cell.get('t')
    if kind == 'inlineStr':
        inline = cell.find('{*}is')
        return _rich_text(inline) if inline is not None else ''
    value = cell.findtext('{*}v', '') or ''
    if kind == 's' and value:
        index = int(value)
        if not 0 <= index < len(strings):
            raise ValueError('엑셀 공유 문자열 참조가 올바르지 않습니다.')
        return strings[index]
    return {'0': 'FALSE', '1': 'TRUE'}.get(value, value) if kind == 'b' else value


def _lines(archive):
    sheets, relationships = _workbook(archive)
    shared = next((rel for rel in relationships.values() if rel.get('Type', '').endswith('/sharedStrings')), None)
    strings = ([_rich_text(item) for item in _xml(archive, _internal_target(WORKBOOK, shared)).findall('{*}si')]
               if shared else [])
    for sheet in sheets:
        relationship = relationships.get(_id(sheet), {})
        if not relationship.get('Type', '').endswith('/worksheet'):
            continue
        path = _internal_target(WORKBOOK, relationship)
        worksheet = _xml(archive, path)
        yield '\n[시트] ' + sheet.get('name', '')
        links = _relationships(archive, path)
        for link in worksheet.findall('{*}hyperlinks/{*}hyperlink'):
            relation = links.get(_id(link), {})
            target = relation.get('Target', '') if relation.get('Type', '').endswith('/hyperlink') else ''
            location = link.get('location')
            destination = target + ('#' + location if location else '')
            if destination:
                yield f"[링크 {link.get('ref', '')}] {destination}"
        for cell in worksheet.iterfind('{*}sheetData/{*}row/{*}c'):
            value = _cell_text(cell, strings)
            if value:
                yield f"{cell.get('r', '?')}\t{value}"


def read_text(data, limit):
    lines, length = [], 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for line in _lines(archive):
            length += len(line) + 1
            if length > limit:
                return '\n'.join(lines), True
            lines.append(line)
    return '\n'.join(lines), False
