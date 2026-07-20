"""Manage OneNote notebooks, sections, and pages via a PowerShell COM bridge."""
import base64
import json
import logging
import subprocess

log = logging.getLogger(__name__)

_ONE_NS = "http://schemas.microsoft.com/office/onenote/2013/onenote"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _run_ps(script: str, timeout: int = 30) -> dict:
    encoded_cmd = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_cmd],
            capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"OneNote COM call timed out after {timeout} s"}
    except FileNotFoundError:
        return {"ok": False, "error": "powershell.exe not found"}

    raw = result.stdout
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    stdout = raw.decode("utf-8", errors="replace").strip()

    if not stdout:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        log.error("OneNote PS bridge stderr: %s", stderr)
        return {"ok": False, "error": stderr or "PowerShell returned no output"}

    start = stdout.find("{")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(stdout, start)
            return obj
        except (json.JSONDecodeError, ValueError):
            pass

    log.error("OneNote PS bridge unexpected output: %s", stdout)
    return {"ok": False, "error": stdout}


# ---------------------------------------------------------------------------
# PowerShell scripts
# ---------------------------------------------------------------------------

# Plain string — no Python format placeholders.
_PS_HIERARCHY = """\
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
    $on  = New-Object -ComObject 'OneNote.Application'
    $xml = ''
    $on.GetHierarchy('', 4, [ref]$xml)
    [xml]$doc = $xml
    $ns = @{one = 'http://schemas.microsoft.com/office/onenote/2013/onenote'}

    function script:Get-Children($parentNode) {
        return @($parentNode.ChildNodes | ForEach-Object {
            $c = $_
            if ($c.LocalName -eq 'Section' -and $c.isInRecycleBin -ne 'true') {
                $pages = @($c.ChildNodes | Where-Object {
                    $_.LocalName -eq 'Page' -and $_.isInRecycleBin -ne 'true'
                } | ForEach-Object {
                    [ordered]@{
                        type     = 'page'
                        name     = if ($_.name) {$_.name} else {'(Untitled)'}
                        id       = $_.ID
                        modified = $_.lastModifiedTime
                    }
                })
                [ordered]@{type='section'; name=$c.name; id=$c.ID; color=$c.color; pages=$pages}
            } elseif ($c.LocalName -eq 'SectionGroup' -and $c.isRecycleBin -ne 'true') {
                [ordered]@{type='section_group'; name=$c.name; id=$c.ID; children=script:Get-Children $c}
            }
        } | Where-Object { $_ -ne $null })
    }

    $notebooks = @(Select-Xml -Xml $doc -XPath '//one:Notebook' -Namespace $ns | ForEach-Object {
        $n = $_.Node
        [ordered]@{type='notebook'; name=$n.name; id=$n.ID; children=script:Get-Children $n}
    })

    ConvertTo-Json @{ok=$true; notebooks=$notebooks} -Depth 15 -Compress
} catch {
    ConvertTo-Json @{ok=$false; error=$_.Exception.Message} -Compress
}
"""

# Format strings — use {{ }} for literal PS braces, {var} for Python substitution.
_PS_CREATE_PAGE = """\
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$sectionId = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{section_id_b64}'))
$title     = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{title_b64}'))
$body      = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{body_b64}'))
try {{
    $on = New-Object -ComObject 'OneNote.Application'
    if (-not $sectionId) {{
        $xmlOut = ''
        $on.GetHierarchy('', 3, [ref]$xmlOut)
        [xml]$hier = $xmlOut
        $ns2 = @{{one = '{ONE_NS}'}}
        $sec = Select-Xml -Xml $hier -XPath '//one:Section' -Namespace $ns2 | Select-Object -First 1
        if (-not $sec) {{
            ConvertTo-Json @{{ok=$false; error='No OneNote section found - open OneNote first'}}
            exit
        }}
        $sectionId = $sec.Node.ID
    }}
    $pageId = ''
    $on.CreateNewPage($sectionId, [ref]$pageId, 1)
    $pageXml = "<one:Page xmlns:one='{ONE_NS}' ID='$pageId'><one:Title><one:OE><one:T><![CDATA[$title]]></one:T></one:OE></one:Title><one:Outline><one:OEChildren><one:OE><one:T><![CDATA[$body]]></one:T></one:OE></one:OEChildren></one:Outline></one:Page>"
    $on.UpdatePageContent($pageXml, [DateTime]::MinValue, 2, $false)
    ConvertTo-Json @{{ok=$true; page_id=$pageId}}
}} catch {{
    ConvertTo-Json @{{ok=$false; error=$_.Exception.Message}}
}}
"""

_PS_GET_PAGE_CONTENT = """\
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$pageId = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{page_id_b64}'))
try {{
    $on = New-Object -ComObject 'OneNote.Application'
    $xml = ''
    $on.GetPageContent($pageId, [ref]$xml, 7)
    [xml]$doc = $xml
    $ns = @{{one = 'http://schemas.microsoft.com/office/onenote/2013/onenote'}}
    $titleNode = Select-Xml -Xml $doc -XPath '//one:Page/one:Title/one:OE/one:T' -Namespace $ns | Select-Object -First 1
    $title = if ($titleNode) {{ $titleNode.Node.InnerText }} else {{ '' }}
    $textLines = @(Select-Xml -Xml $doc -XPath '//one:Outline//one:T' -Namespace $ns | ForEach-Object {{
        $_.Node.InnerText
    }} | Where-Object {{ $_ -ne $null -and $_ -ne '' }})
    $content = $textLines -join "`n"
    ConvertTo-Json @{{ok=$true; page_id=$pageId; title=$title; content=$content}} -Depth 3 -Compress
}} catch {{
    ConvertTo-Json @{{ok=$false; error=$_.Exception.Message}} -Compress
}}
"""

_PS_RENAME_PAGE = """\
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$pageId  = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{page_id_b64}'))
$newName = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{name_b64}'))
try {{
    $on = New-Object -ComObject 'OneNote.Application'
    $xml = ''
    $on.GetPageContent($pageId, [ref]$xml, 7)
    [xml]$doc = $xml
    $nsMgr = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
    $nsMgr.AddNamespace('one', 'http://schemas.microsoft.com/office/onenote/2013/onenote')
    $titleT = $doc.SelectSingleNode('//one:Page/one:Title/one:OE/one:T', $nsMgr)
    if (-not $titleT) {{ throw 'Could not locate title element in page XML' }}
    $titleT.InnerText = $newName
    $on.UpdatePageContent($doc.OuterXml, [DateTime]::MinValue, 2, $false)
    ConvertTo-Json @{{ok=$true; page_id=$pageId; new_name=$newName}} -Compress
}} catch {{
    ConvertTo-Json @{{ok=$false; error=$_.Exception.Message}} -Compress
}}
"""

_PS_RENAME_SECTION = """\
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$sectionId = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{section_id_b64}'))
$newName   = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{name_b64}'))
try {{
    $on = New-Object -ComObject 'OneNote.Application'
    $xml = ''
    $on.GetHierarchy($sectionId, 0, [ref]$xml)
    [xml]$doc = $xml
    $nsMgr = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
    $nsMgr.AddNamespace('one', 'http://schemas.microsoft.com/office/onenote/2013/onenote')
    $section = $doc.SelectSingleNode('//one:Section', $nsMgr)
    if (-not $section) {{ throw 'Section not found with id ' + $sectionId }}
    $section.SetAttribute('name', $newName)
    $on.UpdateHierarchy($doc.OuterXml)
    ConvertTo-Json @{{ok=$true; section_id=$sectionId; new_name=$newName}} -Compress
}} catch {{
    ConvertTo-Json @{{ok=$false; error=$_.Exception.Message}} -Compress
}}
"""

_PS_RENAME_NOTEBOOK = """\
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$notebookId = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{notebook_id_b64}'))
$newName    = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{name_b64}'))
try {{
    $on = New-Object -ComObject 'OneNote.Application'
    $xml = ''
    $on.GetHierarchy($notebookId, 0, [ref]$xml)
    [xml]$doc = $xml
    $nsMgr = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
    $nsMgr.AddNamespace('one', 'http://schemas.microsoft.com/office/onenote/2013/onenote')
    $notebook = $doc.SelectSingleNode('//one:Notebook', $nsMgr)
    if (-not $notebook) {{ throw 'Notebook not found with id ' + $notebookId }}
    $notebook.SetAttribute('name', $newName)
    $on.UpdateHierarchy($doc.OuterXml)
    ConvertTo-Json @{{ok=$true; notebook_id=$notebookId; new_name=$newName}} -Compress
}} catch {{
    ConvertTo-Json @{{ok=$false; error=$_.Exception.Message}} -Compress
}}
"""

_PS_MOVE_PAGE = """\
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$pageId          = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{page_id_b64}'))
$targetSectionId = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{section_id_b64}'))
try {{
    $on = New-Object -ComObject 'OneNote.Application'
    $on.MovePage($pageId, $targetSectionId, '')
    ConvertTo-Json @{{ok=$true; page_id=$pageId; target_section_id=$targetSectionId}} -Compress
}} catch {{
    ConvertTo-Json @{{ok=$false; error=$_.Exception.Message}} -Compress
}}
"""

_PS_CREATE_SECTION = """\
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$notebookId  = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{notebook_id_b64}'))
$sectionName = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{name_b64}'))
try {{
    $on = New-Object -ComObject 'OneNote.Application'
    $xml = ''
    $on.GetHierarchy($notebookId, 0, [ref]$xml)
    [xml]$doc = $xml
    $nsMgr = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
    $nsMgr.AddNamespace('one', 'http://schemas.microsoft.com/office/onenote/2013/onenote')
    $notebook = $doc.SelectSingleNode('//one:Notebook', $nsMgr)
    if (-not $notebook) {{ throw 'Notebook not found with id ' + $notebookId }}
    $notebookPath = $notebook.GetAttribute('path')
    if (-not $notebookPath) {{ throw 'Notebook has no path attribute — it may be a cloud-only notebook' }}
    $sectionPath = Join-Path $notebookPath ($sectionName + '.one')
    $newSectionId = ''
    $on.OpenHierarchy($sectionPath, '', [ref]$newSectionId, 3)
    ConvertTo-Json @{{ok=$true; section_id=$newSectionId; name=$sectionName; path=$sectionPath}} -Compress
}} catch {{
    ConvertTo-Json @{{ok=$false; error=$_.Exception.Message}} -Compress
}}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_hierarchy() -> dict:
    """Return notebooks → sections (and section groups) → pages."""
    return _run_ps(_PS_HIERARCHY, timeout=20)


def create_page(title: str, body: str, section_id: str = "") -> dict:
    """Create a OneNote page. If section_id is empty, uses the first available section."""
    script = _PS_CREATE_PAGE.format(
        section_id_b64=_b64(section_id),
        title_b64=_b64(title),
        body_b64=_b64(body),
        ONE_NS=_ONE_NS,
    )
    return _run_ps(script)


def get_page_content(page_id: str) -> dict:
    """Return the text content of a OneNote page."""
    script = _PS_GET_PAGE_CONTENT.format(page_id_b64=_b64(page_id))
    return _run_ps(script, timeout=20)


def rename_page(page_id: str, new_name: str) -> dict:
    """Rename a OneNote page by updating its title."""
    script = _PS_RENAME_PAGE.format(
        page_id_b64=_b64(page_id),
        name_b64=_b64(new_name),
    )
    return _run_ps(script, timeout=20)


def rename_section(section_id: str, new_name: str) -> dict:
    """Rename a OneNote section."""
    script = _PS_RENAME_SECTION.format(
        section_id_b64=_b64(section_id),
        name_b64=_b64(new_name),
    )
    return _run_ps(script, timeout=20)


def rename_notebook(notebook_id: str, new_name: str) -> dict:
    """Rename a OneNote notebook (display name only)."""
    script = _PS_RENAME_NOTEBOOK.format(
        notebook_id_b64=_b64(notebook_id),
        name_b64=_b64(new_name),
    )
    return _run_ps(script, timeout=20)


def move_page(page_id: str, target_section_id: str) -> dict:
    """Move a page to a different section."""
    script = _PS_MOVE_PAGE.format(
        page_id_b64=_b64(page_id),
        section_id_b64=_b64(target_section_id),
    )
    return _run_ps(script, timeout=20)


def create_section(notebook_id: str, section_name: str) -> dict:
    """Create a new section inside a notebook (requires a local/synced notebook with a path)."""
    script = _PS_CREATE_SECTION.format(
        notebook_id_b64=_b64(notebook_id),
        name_b64=_b64(section_name),
    )
    return _run_ps(script, timeout=20)