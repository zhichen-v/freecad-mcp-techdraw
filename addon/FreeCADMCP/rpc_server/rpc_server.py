import FreeCAD
import FreeCADGui
import ObjectsFem

import contextlib
import ipaddress
import json
import queue
import re
import base64
import io
import os
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any
from xmlrpc.server import SimpleXMLRPCServer

from PySide import QtCore, QtGui, QtWidgets
try:
    from PySide import QtSvg
    HAS_QT_SVG = True
except ImportError:
    HAS_QT_SVG = False

from .parts_library import get_parts_list, insert_part_from_library
from .serialize import serialize_object

rpc_server_thread = None

# TechDraw template shortcut names → blank SVG filenames
TECHDRAW_TEMPLATES = {
    "A0_Landscape": "A0_Landscape_blank.svg",
    "A0_Portrait": "A0_Portrait_blank.svg",
    "A1_Landscape": "A1_Landscape_blank.svg",
    "A1_Portrait": "A1_Portrait_blank.svg",
    "A2_Landscape": "A2_Landscape_blank.svg",
    "A2_Portrait": "A2_Portrait_blank.svg",
    "A3_Landscape": "A3_Landscape_blank.svg",
    "A3_Portrait": "A3_Portrait_blank.svg",
    "A4_Landscape": "A4_Landscape_blank.svg",
    "A4_Portrait": "A4_Portrait_blank.svg",
}
rpc_server_instance = None


# --- Settings persistence ---

_SETTINGS_FILENAME = "freecad_mcp_settings.json"

_DEFAULT_SETTINGS = {
    "remote_enabled": False,
    "allowed_ips": "127.0.0.1",
    "auto_start_rpc": False,
}


def _get_settings_path():
    return os.path.join(FreeCAD.getUserAppDataDir(), _SETTINGS_FILENAME)


def load_settings():
    path = _get_settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                settings = json.load(f)
            # Ensure all default keys exist
            for key, value in _DEFAULT_SETTINGS.items():
                if key not in settings:
                    settings[key] = value
            return settings
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Failed to load MCP settings: {e}\n")
    return dict(_DEFAULT_SETTINGS)


def save_settings(settings):
    path = _get_settings_path()
    try:
        with open(path, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to save MCP settings: {e}\n")


# --- IP-filtered XML-RPC server ---

class FilteredXMLRPCServer(SimpleXMLRPCServer):
    """XML-RPC server that filters connections by allowed IP addresses/subnets."""

    def __init__(self, addr, allowed_ips_str="127.0.0.1", **kwargs):
        self._allowed_networks = _parse_allowed_ips(allowed_ips_str)
        super().__init__(addr, **kwargs)

    def verify_request(self, request, client_address):
        client_ip = client_address[0]
        try:
            addr = ipaddress.ip_address(client_ip)
            for network in self._allowed_networks:
                if addr in network:
                    return True
        except ValueError:
            pass
        FreeCAD.Console.PrintWarning(
            f"MCP RPC: Rejected connection from {client_ip}\n"
        )
        return False


_COMMA_SEP_RE = re.compile(r"^\s*[^,\s]+(\s*,\s*[^,\s]+)*\s*$")


def validate_allowed_ips(allowed_ips_str):
    """Validate a comma-separated string of IP addresses/subnets.

    Returns a ``(valid, errors)`` tuple.  ``valid`` is a list of normalised
    entry strings that passed validation; ``errors`` is a list of
    human-readable error messages (empty when the input is fully valid).

    Checks performed:
    1. The overall string is well-formed comma-separated (no leading/trailing
       commas, no empty entries between commas, not blank).
    2. Each individual entry is a valid IPv4/IPv6 address or CIDR subnet
       (validated via the stdlib ``ipaddress`` module).
    """
    errors = []

    if not allowed_ips_str or not allowed_ips_str.strip():
        return [], ["Input must not be empty."]

    if not _COMMA_SEP_RE.match(allowed_ips_str):
        return [], [
            "Malformed list — check for leading/trailing commas, "
            "double commas, or missing separators."
        ]

    valid = []
    for entry in allowed_ips_str.split(","):
        entry = entry.strip()
        try:
            ipaddress.ip_network(entry, strict=False)
            valid.append(entry)
        except ValueError:
            errors.append(f"Invalid IP/subnet: '{entry}'")
    return valid, errors


def _parse_allowed_ips(allowed_ips_str):
    """Parse a comma-separated string of IPs/subnets into a list of ip_network objects."""
    valid, errors = validate_allowed_ips(allowed_ips_str)
    for msg in errors:
        FreeCAD.Console.PrintWarning(f"MCP RPC: {msg}, skipping\n")
    return [ipaddress.ip_network(entry, strict=False) for entry in valid]

# GUI task queue
rpc_request_queue = queue.Queue()
rpc_response_queue = queue.Queue()


def process_gui_tasks():
    while not rpc_request_queue.empty():
        task = rpc_request_queue.get()
        res = task()
        if res is not None:
            rpc_response_queue.put(res)
    QtCore.QTimer.singleShot(500, process_gui_tasks)


@dataclass
class Object:
    name: str
    type: str | None = None
    analysis: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


def set_object_property(
    doc: FreeCAD.Document, obj: FreeCAD.DocumentObject, properties: dict[str, Any]
):
    for prop, val in properties.items():
        try:
            if prop in obj.PropertiesList:
                if prop == "Placement" and isinstance(val, dict):
                    if "Base" in val:
                        pos = val["Base"]
                    elif "Position" in val:
                        pos = val["Position"]
                    else:
                        pos = {}
                    rot = val.get("Rotation", {})
                    placement = FreeCAD.Placement(
                        FreeCAD.Vector(
                            pos.get("x", 0),
                            pos.get("y", 0),
                            pos.get("z", 0),
                        ),
                        FreeCAD.Rotation(
                            FreeCAD.Vector(
                                rot.get("Axis", {}).get("x", 0),
                                rot.get("Axis", {}).get("y", 0),
                                rot.get("Axis", {}).get("z", 1),
                            ),
                            rot.get("Angle", 0),
                        ),
                    )
                    setattr(obj, prop, placement)

                elif isinstance(getattr(obj, prop), FreeCAD.Vector) and isinstance(
                    val, dict
                ):
                    vector = FreeCAD.Vector(
                        val.get("x", 0), val.get("y", 0), val.get("z", 0)
                    )
                    setattr(obj, prop, vector)

                elif prop in ["Base", "Tool", "Source", "Profile"] and isinstance(
                    val, str
                ):
                    ref_obj = doc.getObject(val)
                    if ref_obj:
                        setattr(obj, prop, ref_obj)
                    else:
                        raise ValueError(f"Referenced object '{val}' not found.")

                elif prop == "References" and isinstance(val, list):
                    refs = []
                    for ref_name, face in val:
                        ref_obj = doc.getObject(ref_name)
                        if ref_obj:
                            refs.append((ref_obj, face))
                        else:
                            raise ValueError(f"Referenced object '{ref_name}' not found.")
                    setattr(obj, prop, refs)

                else:
                    setattr(obj, prop, val)
            # ShapeColor is a property of the ViewObject
            elif prop == "ShapeColor" and isinstance(val, (list, tuple)):
                setattr(obj.ViewObject, prop, (float(val[0]), float(val[1]), float(val[2]), float(val[3])))

            elif prop == "ViewObject" and isinstance(val, dict):
                for k, v in val.items():
                    if k == "ShapeColor":
                        setattr(obj.ViewObject, k, (float(v[0]), float(v[1]), float(v[2]), float(v[3])))
                    else:
                        setattr(obj.ViewObject, k, v)

            else:
                setattr(obj, prop, val)

        except Exception as e:
            FreeCAD.Console.PrintError(f"Property '{prop}' assignment error: {e}\n")


class FreeCADRPC:
    """RPC server for FreeCAD"""

    def ping(self):
        return True

    def create_document(self, name="New_Document"):
        rpc_request_queue.put(lambda: self._create_document_gui(name))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "document_name": name}
        else:
            return {"success": False, "error": res}

    def create_object(self, doc_name, obj_data: dict[str, Any]):
        obj = Object(
            name=obj_data.get("Name", "New_Object"),
            type=obj_data["Type"],
            analysis=obj_data.get("Analysis", None),
            properties=obj_data.get("Properties", {}),
        )
        rpc_request_queue.put(lambda: self._create_object_gui(doc_name, obj))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "object_name": obj.name}
        else:
            return {"success": False, "error": res}

    def edit_object(self, doc_name: str, obj_name: str, properties: dict[str, Any]) -> dict[str, Any]:
        obj = Object(
            name=obj_name,
            properties=properties.get("Properties", {}),
        )
        rpc_request_queue.put(lambda: self._edit_object_gui(doc_name, obj))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "object_name": obj.name}
        else:
            return {"success": False, "error": res}

    def delete_object(self, doc_name: str, obj_name: str):
        rpc_request_queue.put(lambda: self._delete_object_gui(doc_name, obj_name))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "object_name": obj_name}
        else:
            return {"success": False, "error": res}

    def execute_code(self, code: str) -> dict[str, Any]:
        output_buffer = io.StringIO()
        def task():
            try:
                with contextlib.redirect_stdout(output_buffer):
                    exec(code, globals())
                FreeCAD.Console.PrintMessage("Python code executed successfully.\n")
                return True
            except Exception as e:
                FreeCAD.Console.PrintError(
                    f"Error executing Python code: {e}\n"
                )
                return f"Error executing Python code: {e}\n"

        rpc_request_queue.put(task)
        res = rpc_response_queue.get()
        if res is True:
            return {
                "success": True,
                "message": "Python code execution scheduled. \nOutput: " + output_buffer.getvalue()
            }
        else:
            return {"success": False, "error": res}

    def get_objects(self, doc_name):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            return [serialize_object(obj) for obj in doc.Objects]
        else:
            return []

    def get_object(self, doc_name, obj_name):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            return serialize_object(doc.getObject(obj_name))
        else:
            return None

    def insert_part_from_library(self, relative_path):
        rpc_request_queue.put(lambda: self._insert_part_from_library(relative_path))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "message": "Part inserted from library."}
        else:
            return {"success": False, "error": res}

    def list_documents(self):
        return list(FreeCAD.listDocuments().keys())

    def get_parts_list(self):
        return get_parts_list()

    def create_techdraw_page(self, doc_name, page_name, template):
        rpc_request_queue.put(lambda: self._create_techdraw_page_gui(doc_name, page_name, template))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "page_name": page_name}
        else:
            return {"success": False, "error": str(res)}

    def add_projection_group(self, doc_name, page_name, options):
        rpc_request_queue.put(lambda: self._add_projection_group_gui(doc_name, page_name, options))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "group_name": options.get("group_name", "ProjGroup")}
        else:
            return {"success": False, "error": str(res)}

    def add_techdraw_view(self, doc_name, page_name, options):
        rpc_request_queue.put(lambda: self._add_techdraw_view_gui(doc_name, page_name, options))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "view_name": options.get("view_name", "View")}
        else:
            return {"success": False, "error": str(res)}

    def get_active_screenshot(self, view_name: str = "Isometric", width: int | None = None, height: int | None = None, focus_object: str | None = None) -> str:
        """Get a screenshot of the active view.
        
        Returns a base64-encoded string of the screenshot or None if a screenshot
        cannot be captured (e.g., when in TechDraw or Spreadsheet view).
        """
        # First check if the active view supports screenshots
        def check_view_supports_screenshots():
            try:
                active_view = FreeCADGui.ActiveDocument.ActiveView
                if active_view is None:
                    FreeCAD.Console.PrintWarning("No active view available\n")
                    return False
                
                view_type = type(active_view).__name__
                has_save_image = hasattr(active_view, 'saveImage')
                FreeCAD.Console.PrintMessage(f"View type: {view_type}, Has saveImage: {has_save_image}\n")
                return has_save_image
            except Exception as e:
                FreeCAD.Console.PrintError(f"Error checking view capabilities: {e}\n")
                return False
                
        rpc_request_queue.put(check_view_supports_screenshots)
        supports_screenshots = rpc_response_queue.get()
        
        if not supports_screenshots:
            FreeCAD.Console.PrintWarning("Current view does not support screenshots\n")
            return None
            
        # If view supports screenshots, proceed with capture
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        rpc_request_queue.put(
            lambda: self._save_active_screenshot(tmp_path, view_name, width, height, focus_object)
        )
        res = rpc_response_queue.get()
        if res is True:
            try:
                with open(tmp_path, "rb") as image_file:
                    image_bytes = image_file.read()
                    encoded = base64.b64encode(image_bytes).decode("utf-8")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            return encoded
        else:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            FreeCAD.Console.PrintWarning(f"Failed to capture screenshot: {res}\n")
            return None

    def get_techdraw_screenshot(self, doc_name: str, page_name: str, width: int = 1920) -> str:
        """Get a screenshot of a TechDraw page by rendering its SVG result to PNG.

        Returns a base64-encoded PNG string, or None on failure.
        """
        if not HAS_QT_SVG:
            FreeCAD.Console.PrintWarning("QtSvg not available, cannot render TechDraw page\n")
            return None

        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        rpc_request_queue.put(
            lambda: self._get_techdraw_screenshot_gui(doc_name, page_name, tmp_path, width)
        )
        res = rpc_response_queue.get()
        if res is True:
            try:
                with open(tmp_path, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("utf-8")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            return encoded
        else:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            error_msg = f"Failed to capture TechDraw screenshot: {res}"
            FreeCAD.Console.PrintWarning(error_msg + "\n")
            raise RuntimeError(error_msg)

    @staticmethod
    def _fix_techdraw_svg_template_scale(svg_path: str):
        """Fix TechDraw SVG export where template group gets an incorrect scale transform.

        FreeCAD's ``TechDrawGui.exportPageAsSvg()`` wraps the template content
        in a ``<g transform="scale(10, 10)">`` group, assuming the template
        coordinates are in mm.  However, custom templates whose coordinates are
        already at the viewBox scale (e.g. viewBox ``0 0 3000 2100`` with
        coordinates in the 0–3000 range) will be scaled 10× beyond the
        viewBox, causing only the top-left corner to render.

        This method detects the situation by comparing the first numeric
        coordinate found inside the template group against the viewBox width.
        If the coordinate is already larger than half the viewBox width, the
        ``scale(10, …)`` transform is redundant and is removed.
        """
        import re

        try:
            with open(svg_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Find the viewBox width
            vb_match = re.search(r'viewBox="([^"]+)"', content)
            if not vb_match:
                return
            vb_parts = vb_match.group(1).split()
            if len(vb_parts) < 3:
                return
            vb_width = float(vb_parts[2])

            # Find the template group with a scale(10, 10) transform
            pattern = r'transform="scale\(10\.0+,\s*10\.0+\)"'
            scale_match = re.search(pattern, content)
            if not scale_match:
                return

            # Find the first numeric coordinate inside the template group
            # (look for a point value in a polyline/line/rect after the scale transform)
            after_scale = content[scale_match.end():]
            coord_match = re.search(r'points="([\d.]+)', after_scale)
            if not coord_match:
                return
            first_coord = float(coord_match.group(1))

            # If the coordinate is already at viewBox scale, remove the scale transform
            if first_coord > vb_width * 0.5:
                content = content[:scale_match.start()] + content[scale_match.end():]
                with open(svg_path, "w", encoding="utf-8") as f:
                    f.write(content)
        except Exception:
            pass  # non-critical; fall back to original SVG

    def _get_techdraw_screenshot_gui(self, doc_name: str, page_name: str, save_path: str, width: int = 1920):
        try:
            import TechDrawGui

            doc = FreeCAD.getDocument(doc_name)
            if doc is None:
                return f"Document '{doc_name}' not found"
            page = doc.getObject(page_name)
            if page is None:
                return f"Page '{page_name}' not found in '{doc_name}'"

            # Export SVG via TechDrawGui (FreeCAD 1.0 compatible)
            fd, svg_path = tempfile.mkstemp(suffix=".svg")
            os.close(fd)
            try:
                TechDrawGui.exportPageAsSvg(page, svg_path)

                if not os.path.exists(svg_path) or os.path.getsize(svg_path) == 0:
                    return f"Failed to export SVG for page '{page_name}'"

                # Fix template scale transform if needed
                self._fix_techdraw_svg_template_scale(svg_path)

                renderer = QtSvg.QSvgRenderer(svg_path)
                if not renderer.isValid():
                    return f"Failed to load exported SVG for page '{page_name}'"

                # Calculate height proportionally from the SVG's default size
                default_size = renderer.defaultSize()
                if default_size.width() > 0:
                    height = int(width * default_size.height() / default_size.width())
                else:
                    height = int(width * 0.707)  # fallback to A4 ratio

                image = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
                image.fill(QtCore.Qt.white)
                painter = QtGui.QPainter(image)
                renderer.render(painter)
                painter.end()

                image.save(save_path)
                return True
            finally:
                if os.path.exists(svg_path):
                    os.remove(svg_path)
        except Exception as e:
            return str(e)

    def _create_document_gui(self, name):
        doc = FreeCAD.newDocument(name)
        doc.recompute()
        FreeCAD.Console.PrintMessage(f"Document '{name}' created via RPC.\n")
        return True

    def _create_object_gui(self, doc_name, obj: Object):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            try:
                if obj.type == "Fem::FemMeshGmsh" and obj.analysis:
                    from femmesh.gmshtools import GmshTools
                    res = getattr(doc, obj.analysis).addObject(ObjectsFem.makeMeshGmsh(doc, obj.name))[0]
                    if "Part" in obj.properties:
                        target_obj = doc.getObject(obj.properties["Part"])
                        if target_obj:
                            res.Part = target_obj
                        else:
                            raise ValueError(f"Referenced object '{obj.properties['Part']}' not found.")
                        del obj.properties["Part"]
                    else:
                        raise ValueError("'Part' property not found in properties.")

                    for param, value in obj.properties.items():
                        if hasattr(res, param):
                            setattr(res, param, value)
                    doc.recompute()

                    gmsh_tools = GmshTools(res)
                    gmsh_tools.create_mesh()
                    FreeCAD.Console.PrintMessage(
                        f"FEM Mesh '{res.Name}' generated successfully in '{doc_name}'.\n"
                    )
                elif obj.type.startswith("Fem::"):
                    fem_make_methods = {
                        "MaterialCommon": ObjectsFem.makeMaterialSolid,
                        "AnalysisPython": ObjectsFem.makeAnalysis,
                    }
                    obj_type_short = obj.type.split("::")[1]
                    method_name = "make" + obj_type_short
                    make_method = fem_make_methods.get(obj_type_short, getattr(ObjectsFem, method_name, None))

                    if callable(make_method):
                        res = make_method(doc, obj.name)
                        set_object_property(doc, res, obj.properties)
                        FreeCAD.Console.PrintMessage(
                            f"FEM object '{res.Name}' created with '{method_name}'.\n"
                        )
                    else:
                        raise ValueError(f"No creation method '{method_name}' found in ObjectsFem.")
                    if obj.type != "Fem::AnalysisPython" and obj.analysis:
                        getattr(doc, obj.analysis).addObject(res)
                else:
                    res = doc.addObject(obj.type, obj.name)
                    set_object_property(doc, res, obj.properties)
                    FreeCAD.Console.PrintMessage(
                        f"{res.TypeId} '{res.Name}' added to '{doc_name}' via RPC.\n"
                    )
 
                doc.recompute()
                return True
            except Exception as e:
                return str(e)
        else:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

    def _edit_object_gui(self, doc_name: str, obj: Object):
        doc = FreeCAD.getDocument(doc_name)
        if not doc:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

        obj_ins = doc.getObject(obj.name)
        if not obj_ins:
            FreeCAD.Console.PrintError(f"Object '{obj.name}' not found in document '{doc_name}'.\n")
            return f"Object '{obj.name}' not found in document '{doc_name}'.\n"

        try:
            # For Fem::ConstraintFixed
            if hasattr(obj_ins, "References") and "References" in obj.properties:
                refs = []
                for ref_name, face in obj.properties["References"]:
                    ref_obj = doc.getObject(ref_name)
                    if ref_obj:
                        refs.append((ref_obj, face))
                    else:
                        raise ValueError(f"Referenced object '{ref_name}' not found.")
                obj_ins.References = refs
                FreeCAD.Console.PrintMessage(
                    f"References updated for '{obj.name}' in '{doc_name}'.\n"
                )
                # delete References from properties
                del obj.properties["References"]
            set_object_property(doc, obj_ins, obj.properties)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Object '{obj.name}' updated via RPC.\n")
            return True
        except Exception as e:
            return str(e)

    def _delete_object_gui(self, doc_name: str, obj_name: str):
        doc = FreeCAD.getDocument(doc_name)
        if not doc:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

        try:
            doc.removeObject(obj_name)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Object '{obj_name}' deleted via RPC.\n")
            return True
        except Exception as e:
            return str(e)

    def _insert_part_from_library(self, relative_path):
        try:
            insert_part_from_library(relative_path)
            return True
        except Exception as e:
            return str(e)

    def _resolve_template_path(self, template):
        """Resolve a template shortcut name or absolute path to a full SVG path.

        When *template* is empty (``""``), the user's default template
        configured in FreeCAD Preferences → TechDraw → General is used.

        Returns the resolved path string, or a list of available shortcut names
        if the template cannot be found.
        """
        # Empty string → use FreeCAD's user-configured default template
        if not template:
            param = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Files")
            default_path = param.GetString("TemplateFile", "")
            if default_path and os.path.exists(default_path):
                return default_path
            # Fallback: try the built-in A4 Landscape blank
            templates_dir = os.path.join(
                FreeCAD.getResourceDir(), "Mod", "TechDraw", "Templates"
            )
            fallback = os.path.join(templates_dir, "A4_Landscape_blank.svg")
            if os.path.exists(fallback):
                return fallback

        # Absolute path given and exists → use as-is
        if os.path.isabs(template) and os.path.exists(template):
            return template

        templates_dir = os.path.join(
            FreeCAD.getResourceDir(), "Mod", "TechDraw", "Templates"
        )

        # Shortcut name lookup
        filename = TECHDRAW_TEMPLATES.get(template)
        if filename:
            path = os.path.join(templates_dir, filename)
            if os.path.exists(path):
                return path

        # Fallback: list available shortcuts / SVG files
        if os.path.exists(templates_dir):
            available_files = [f for f in os.listdir(templates_dir) if f.endswith(".svg")]
            available_shortcuts = list(TECHDRAW_TEMPLATES.keys())
            return available_shortcuts + available_files
        return list(TECHDRAW_TEMPLATES.keys())

    def _create_techdraw_page_gui(self, doc_name, page_name, template):
        try:
            doc = FreeCAD.getDocument(doc_name)
            if not doc:
                return f"Document '{doc_name}' not found."

            template_path = self._resolve_template_path(template)
            if isinstance(template_path, list):
                return (
                    f"Template '{template}' not found. "
                    f"Available shortcuts: {', '.join(TECHDRAW_TEMPLATES.keys())}. "
                    f"You may also pass an absolute SVG path."
                )

            page = doc.addObject("TechDraw::DrawPage", page_name)
            tmpl_obj = doc.addObject("TechDraw::DrawSVGTemplate", page_name + "_Template")
            tmpl_obj.Template = template_path
            page.Template = tmpl_obj
            doc.recompute()

            FreeCAD.Console.PrintMessage(
                f"TechDraw page '{page_name}' created in '{doc_name}'.\n"
            )
            return True
        except Exception as e:
            return str(e)

    def _add_projection_group_gui(self, doc_name, page_name, options):
        try:
            doc = FreeCAD.getDocument(doc_name)
            if not doc:
                return f"Document '{doc_name}' not found."

            page = doc.getObject(page_name)
            if not page:
                return f"Page '{page_name}' not found in document '{doc_name}'."

            # Validate that the page has a properly configured Template
            if not hasattr(page, "Template") or page.Template is None:
                return f"Page '{page_name}' has no Template set. Create the page with create_techdraw_page first."
            tmpl = page.Template
            if not hasattr(tmpl, "Template") or not tmpl.Template:
                return f"Page '{page_name}' has an empty Template path. The template SVG may not have loaded correctly."

            source_names = options.get("source_objects", [])
            projections = options.get("projections", ["Front", "Top", "Right", "FrontTopRight"])
            projection_type = options.get("projection_type", "Third Angle")
            scale = float(options.get("scale", 1.0))
            x = float(options.get("x", 0.0))
            y = float(options.get("y", 0.0))
            group_name = options.get("group_name", "ProjGroup")
            anchor_direction = options.get("anchor_direction", [0, -1, 0])
            anchor_rotation_vector = options.get("anchor_rotation_vector", [0, 0, 1])

            sources = []
            for name in source_names:
                obj = doc.getObject(name)
                if not obj:
                    return f"Source object '{name}' not found in document '{doc_name}'."
                sources.append(obj)

            group = doc.addObject("TechDraw::DrawProjGroup", group_name)
            page.addView(group)
            group.Source = sources
            group.ProjectionType = projection_type
            group.ScaleType = "Custom"
            group.Scale = scale
            group.X = x
            group.Y = y

            # Anchor (Front) must be added first
            anchor = group.addProjection("Front")
            anchor.Direction = FreeCAD.Vector(*anchor_direction)
            anchor.RotationVector = FreeCAD.Vector(*anchor_rotation_vector)

            valid_projections = {
                "Front", "Left", "Right", "Top", "Bottom", "Rear",
                "FrontTopLeft", "FrontTopRight", "FrontBottomLeft", "FrontBottomRight",
            }
            for proj in projections:
                if proj == "Front":
                    continue  # already added as anchor
                if proj in valid_projections:
                    group.addProjection(proj)
                else:
                    FreeCAD.Console.PrintWarning(f"Unknown projection '{proj}', skipping.\n")

            doc.recompute()
            FreeCAD.Console.PrintMessage(
                f"Projection group '{group_name}' added to page '{page_name}'.\n"
            )
            return True
        except Exception as e:
            return str(e)

    def _add_techdraw_view_gui(self, doc_name, page_name, options):
        try:
            doc = FreeCAD.getDocument(doc_name)
            if not doc:
                return f"Document '{doc_name}' not found."

            page = doc.getObject(page_name)
            if not page:
                return f"Page '{page_name}' not found in document '{doc_name}'."

            source_name = options.get("source_object")
            view_name = options.get("view_name", "View")
            direction = options.get("direction", [0, -1, 0])
            scale = float(options.get("scale", 1.0))
            x = float(options.get("x", 0.0))
            y = float(options.get("y", 0.0))

            source = doc.getObject(source_name)
            if not source:
                return f"Source object '{source_name}' not found in document '{doc_name}'."

            view = doc.addObject("TechDraw::DrawViewPart", view_name)
            page.addView(view)
            view.Source = [source]
            view.Direction = FreeCAD.Vector(*direction)
            view.ScaleType = "Custom"
            view.Scale = scale
            view.X = x
            view.Y = y

            doc.recompute()
            FreeCAD.Console.PrintMessage(
                f"TechDraw view '{view_name}' added to page '{page_name}'.\n"
            )
            return True
        except Exception as e:
            return str(e)

    def _save_active_screenshot(self, save_path: str, view_name: str = "Isometric", width: int | None = None, height: int | None = None, focus_object: str | None = None):
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            # Check if the view supports screenshots
            if not hasattr(view, 'saveImage'):
                return "Current view does not support screenshots"
                
            if view_name == "Isometric":
                view.viewIsometric()
            elif view_name == "Front":
                view.viewFront()
            elif view_name == "Top":
                view.viewTop()
            elif view_name == "Right":
                view.viewRight()
            elif view_name == "Back":
                view.viewBack()
            elif view_name == "Left":
                view.viewLeft()
            elif view_name == "Bottom":
                view.viewBottom()
            elif view_name == "Dimetric":
                view.viewDimetric()
            elif view_name == "Trimetric":
                view.viewTrimetric()
            else:
                raise ValueError(f"Invalid view name: {view_name}")

            # Focus on specific object or fit all
            if focus_object:
                doc = FreeCAD.ActiveDocument
                obj = doc.getObject(focus_object) if doc else None
                if obj:
                    FreeCADGui.Selection.clearSelection()
                    FreeCADGui.Selection.addSelection(obj)
                    FreeCADGui.SendMsgToActiveView("ViewSelection")
                else:
                    view.fitAll()
            else:
                view.fitAll()
            if width is not None and height is not None:
                view.saveImage(save_path, width, height)
            else:
                view.saveImage(save_path)
            return True
        except Exception as e:
            return str(e)


def start_rpc_server(port=9875):
    global rpc_server_thread, rpc_server_instance

    if rpc_server_instance:
        return "RPC Server already running."

    settings = load_settings()
    remote_enabled = settings.get("remote_enabled", False)
    allowed_ips = settings.get("allowed_ips", "127.0.0.1")

    if remote_enabled:
        host = "0.0.0.0"
    else:
        host = "localhost"

    rpc_server_instance = FilteredXMLRPCServer(
        (host, port), allowed_ips_str=allowed_ips, allow_none=True, logRequests=False
    )
    rpc_server_instance.register_instance(FreeCADRPC())

    def server_loop():
        FreeCAD.Console.PrintMessage(f"RPC Server started at {host}:{port}\n")
        if remote_enabled:
            FreeCAD.Console.PrintMessage(f"Remote connections enabled. Allowed IPs: {allowed_ips}\n")
        rpc_server_instance.serve_forever()

    rpc_server_thread = threading.Thread(target=server_loop, daemon=True)
    rpc_server_thread.start()

    QtCore.QTimer.singleShot(500, process_gui_tasks)

    msg = f"RPC Server started at {host}:{port}."
    if remote_enabled:
        msg += f" Allowed IPs: {allowed_ips}"
    return msg


def stop_rpc_server():
    global rpc_server_instance, rpc_server_thread

    if rpc_server_instance:
        rpc_server_instance.shutdown()
        rpc_server_thread.join()
        rpc_server_instance = None
        rpc_server_thread = None
        FreeCAD.Console.PrintMessage("RPC Server stopped.\n")
        return "RPC Server stopped."

    return "RPC Server was not running."


class StartRPCServerCommand:
    def GetResources(self):
        return {"MenuText": "Start RPC Server", "ToolTip": "Start RPC Server"}

    def Activated(self):
        msg = start_rpc_server()
        FreeCAD.Console.PrintMessage(msg + "\n")

    def IsActive(self):
        return True


class StopRPCServerCommand:
    def GetResources(self):
        return {"MenuText": "Stop RPC Server", "ToolTip": "Stop RPC Server"}

    def Activated(self):
        msg = stop_rpc_server()
        FreeCAD.Console.PrintMessage(msg + "\n")

    def IsActive(self):
        return True


class ToggleRemoteConnectionsCommand:
    def GetResources(self):
        return {
            "MenuText": "Remote Connections",
            "ToolTip": "Enable or disable remote connections for the RPC server.",
            "Checkable": True,
        }

    def Activated(self, checked=0):
        settings = load_settings()
        settings["remote_enabled"] = bool(checked)
        save_settings(settings)

        if settings["remote_enabled"]:
            allowed_ips = settings.get("allowed_ips", "127.0.0.1")
            FreeCAD.Console.PrintMessage(
                f"Remote connections enabled. Allowed IPs: {allowed_ips}\n"
            )
        else:
            FreeCAD.Console.PrintMessage("Remote connections disabled.\n")

        if rpc_server_instance:
            FreeCAD.Console.PrintMessage(
                "Restart the RPC server for changes to take effect.\n"
            )

    def IsActive(self):
        return True


class ConfigureAllowedIPsCommand:
    def GetResources(self):
        return {
            "MenuText": "Configure Allowed IPs",
            "ToolTip": "Set which IP addresses or subnets are allowed to connect to the RPC server.",
        }

    def Activated(self):
        settings = load_settings()
        current_ips = settings.get("allowed_ips", "127.0.0.1")
        text, ok = QtWidgets.QInputDialog.getText(
            None,
            "Allowed IP Addresses",
            "Enter allowed IP addresses or subnets (comma-separated):\n"
            "Examples: 127.0.0.1, 192.168.1.0/24, 10.0.0.5",
            QtWidgets.QLineEdit.Normal,
            current_ips,
        )
        if ok and text.strip():
            valid, errors = validate_allowed_ips(text.strip())
            if errors:
                QtWidgets.QMessageBox.warning(
                    None,
                    "Invalid IP Configuration",
                    "The following errors were found:\n\n"
                    + "\n".join(f"• {e}" for e in errors)
                    + ("\n\nOnly valid entries will be saved."
                       if valid else "\n\nNo valid entries found. Settings not changed."),
                )
            if not valid:
                FreeCAD.Console.PrintWarning("Allowed IPs not changed — no valid entries.\n")
                return
            normalised = ", ".join(valid)
            settings["allowed_ips"] = normalised
            save_settings(settings)
            FreeCAD.Console.PrintMessage(
                f"Allowed IPs updated to: {normalised}\n"
            )
            if rpc_server_instance:
                FreeCAD.Console.PrintMessage(
                    "Restart the RPC server for changes to take effect.\n"
                )
        else:
            FreeCAD.Console.PrintMessage("Allowed IPs not changed.\n")

    def IsActive(self):
        return True


class ToggleAutoStartCommand:
    def GetResources(self):
        return {
            "MenuText": "Auto-Start Server",
            "ToolTip": "Automatically start the RPC server when FreeCAD launches.",
            "Checkable": True,
        }

    def Activated(self, checked=0):
        settings = load_settings()
        settings["auto_start_rpc"] = bool(checked)
        save_settings(settings)

        if settings["auto_start_rpc"]:
            FreeCAD.Console.PrintMessage(
                "MCP RPC server will start automatically on next FreeCAD launch.\n"
            )
        else:
            FreeCAD.Console.PrintMessage(
                "MCP RPC server auto-start disabled.\n"
            )

    def IsActive(self):
        return True


FreeCADGui.addCommand("Start_RPC_Server", StartRPCServerCommand())
FreeCADGui.addCommand("Stop_RPC_Server", StopRPCServerCommand())
FreeCADGui.addCommand("Toggle_Auto_Start", ToggleAutoStartCommand())
FreeCADGui.addCommand("Toggle_Remote_Connections", ToggleRemoteConnectionsCommand())
FreeCADGui.addCommand("Configure_Allowed_IPs", ConfigureAllowedIPsCommand())


def _sync_toggle_states():
    """Sync checkable menu items with saved settings on startup."""
    try:
        settings = load_settings()
        main_window = FreeCADGui.getMainWindow()
        toggle_map = {
            "Remote Connections": settings.get("remote_enabled", False),
            "Auto-Start Server": settings.get("auto_start_rpc", False),
        }
        found = 0
        for action in main_window.findChildren(QtWidgets.QAction):
            if action.text() in toggle_map:
                action.setChecked(toggle_map[action.text()])
                found += 1
                if found == len(toggle_map):
                    return
    except Exception:
        pass
    # Retry if menu not ready yet
    QtCore.QTimer.singleShot(2000, _sync_toggle_states)


QtCore.QTimer.singleShot(2000, _sync_toggle_states)