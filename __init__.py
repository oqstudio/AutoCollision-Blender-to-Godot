bl_info = {
    "name": "AutoCollision & Navmesh Ultimate",
    "blender": (4, 1, 0),
    "category": "Object",
    "author": "OQ Studio",
    "version": (4, 0),
    "description": "Professional toolset for Godot 4.x. One-click collisions and stable Navmesh generation.",
    "location": "View3D > Sidebar > Collisions",
    "doc_url": "https://github.com/oqstudio/AutoCollision-Blender-to-Godot",
    "tracker_url": "https://github.com/oqstudio/AutoCollision-Blender-to-Godot/issues",
    "license": "GNU General Public License v3.0",
}

import bpy
import bmesh
import math
import os
import bpy.utils.previews
from bpy.props import BoolProperty, EnumProperty, PointerProperty, FloatProperty, StringProperty
from bpy.types import Operator, PropertyGroup, Panel, AddonPreferences
from mathutils import Vector

# Przechowywanie kolekcji ikon
preview_collections = {}

# --- ADDON PREFERENCES ---
class OQ_STUDIO_AddonPreferences(AddonPreferences):
    bl_idname = __name__

    def draw(self, context):
        layout = self.layout
        
        # Próba wyświetlenia logo
        pcoll = preview_collections.get("main")
        if pcoll and "my_logo" in pcoll:
            row = layout.row()
            row.alignment = 'CENTER'
            # Skalowanie logo w preferencjach
            row.template_icon(icon_value=pcoll["my_logo"].icon_id, scale=5.0) 
        
        column = layout.column(align=True)
        column.label(text="AutoCollision & Navmesh Ultimate", icon='SOLO_ON')
        column.label(text="Developed by OQ Studio (oqstudio.github.io)")
        column.separator()
        
        box = column.box()
        box.label(text="Godot 4.x Optimization Workflow:", icon='INFO')
        box.label(text="• Automatic Collisions")
        box.label(text="• Intelligent Navmesh Generation")
        
        row = box.row(align=True)
        row.operator("wm.url_open", text="GitHub Repository", icon='HOME').url = "https://github.com/oqstudio/AutoCollision-Blender-to-Godot"
        row.operator("wm.url_open", text="Report a Bug", icon='URL').url = "https://github.com/oqstudio/AutoCollision-Blender-to-Godot/issues"
        row.operator("wm.url_open", text="OQ Studio Site", icon='URL').url = "https://oqstudio.github.io"
        
        column.separator()
        column.label(text="License: GNU General Public License v3.0", icon='GHOST_ENABLED')

# --- HELPERY (is_valid, materials, bbox itp.) ---
def is_valid(obj):
    try:
        return obj is not None and obj.name in bpy.data.objects
    except (ReferenceError, AttributeError):
        return False

def assign_transparent_material(obj, name, color_rgba):
    mat = bpy.data.materials.get(name)
    if not mat:
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        if mat.node_tree:
            nodes = mat.node_tree.nodes
            bsdf = nodes.get("Principled BSDF")
            if bsdf:
                if 'Base Color' in bsdf.inputs: bsdf.inputs['Base Color'].default_value = color_rgba
                if 'Alpha' in bsdf.inputs: bsdf.inputs['Alpha'].default_value = color_rgba[3]
                if 'Roughness' in bsdf.inputs: bsdf.inputs['Roughness'].default_value = 1.0
    mat.diffuse_color = color_rgba
    mat.roughness = 1.0
    try: mat.blend_method = 'BLEND'
    except AttributeError: pass
    mat.show_transparent_back = False 
    if obj.data.materials: obj.data.materials[0] = mat
    else: obj.data.materials.append(mat)

def get_world_bbox(obj):
    if bpy.context.view_layer: bpy.context.view_layer.update()
    try:
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    except AttributeError:
        return Vector((0,0,0)), Vector((0,0,0))
    min_x = min(c.x for c in corners)
    max_x = max(c.x for c in corners)
    min_y = min(c.y for c in corners)
    max_y = max(c.y for c in corners)
    min_z = min(c.z for c in corners)
    max_z = max(c.z for c in corners)
    return Vector((min_x, min_y, min_z)), Vector((max_x, max_y, max_z))

def dist_between_bboxes(obj1, obj2):
    min1, max1 = get_world_bbox(obj1)
    min2, max2 = get_world_bbox(obj2)
    dx = max(0, min1.x - max2.x, min2.x - max1.x)
    dy = max(0, min1.y - max2.y, min2.y - max1.y)
    dz = max(0, min1.z - max2.z, min2.z - max1.z)
    return math.sqrt(dx*dx + dy*dy + dz*dz)

def group_objects_by_bbox_distance(objects, threshold):
    if not objects: return []
    if len(objects) == 1: return [objects]
    adj = {i: [] for i in range(len(objects))}
    for i in range(len(objects)):
        for j in range(i + 1, len(objects)):
            dist = dist_between_bboxes(objects[i], objects[j])
            if dist <= threshold:
                adj[i].append(j)
                adj[j].append(i)
    visited = [False] * len(objects)
    groups = []
    for i in range(len(objects)):
        if not visited[i]:
            component = []
            stack = [i]
            visited[i] = True
            while stack:
                curr = stack.pop()
                component.append(objects[curr])
                for neighbor in adj[curr]:
                    if not visited[neighbor]:
                        visited[neighbor] = True
                        stack.append(neighbor)
            groups.append(component)
    return groups

def duplicate_and_merge(context, objects, method='JOIN'):
    bpy.ops.object.select_all(action='DESELECT')
    if method == 'JOIN':
        duplicates = []
        for obj in objects:
            if not is_valid(obj): continue
            new_obj = obj.copy()
            new_obj.data = obj.data.copy()
            context.collection.objects.link(new_obj)
            new_obj.matrix_world = obj.matrix_world.copy()
            new_obj.select_set(True)
            duplicates.append(new_obj)
        if not duplicates: return None
        context.view_layer.objects.active = duplicates[0]
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        bpy.ops.object.join()
        return context.active_object
    return None # Boolean skip for brevity

# --- CLEANING HELPERS ---
def is_collision_name(name):
    suffixes = ['-col', '-colonly', '-navmesh']
    for s in suffixes:
        if name.endswith(s) or s + "." in name: return True
    return False

def remove_existing_collision(obj):
    if not is_valid(obj): return
    to_del = {c for c in obj.children if is_valid(c) and is_collision_name(c.name)}
    for victim in to_del:
        bpy.data.objects.remove(victim, do_unlink=True)

def remove_existing_navmesh(obj):
    if not is_valid(obj): return
    to_del = {c for c in obj.children if is_valid(c) and "-navmesh" in c.name}
    for victim in to_del:
        bpy.data.objects.remove(victim, do_unlink=True)

# --- PROPERTIES ---
class CollisionGeneratorProperties(PropertyGroup):
    merge_selected: BoolProperty(name="Merge Selected", default=False)
    merge_distance: FloatProperty(name="Merge Dist", default=0.05, min=0.0, unit='LENGTH')
    collision_suffix: EnumProperty(items=[('-col', "-col", ""), ('-colonly', "-colonly", "")], default='-col')
    collision_detail: EnumProperty(name="Detail", items=[('BOUNDS', "Bounding Box", ""), ('CONVEX', "Convex Hull", ""), ('EXACT', "Exact Copy", "")], default='BOUNDS')
    nav_max_angle: FloatProperty(name="Max Slope", default=45.0, max=89.9, subtype='ANGLE')
    nav_offset: FloatProperty(name="Lift (m)", default=0.05, unit='LENGTH')
    nav_decimation: FloatProperty(name="Simplify", default=0.8, min=0.01, max=1.0, subtype='FACTOR')

# --- OPERATORS ---
class OBJECT_OT_generate_collision(Operator):
    bl_idname = "object.generate_collision"
    bl_label = "Generate Collision"
    bl_options = {'REGISTER', 'UNDO'}
    all_objects: BoolProperty(name="For all objects", default=False)

    def execute(self, context):
        props = context.scene.collision_generator_props
        # Logika tworzenia kolizji...
        self.report({'INFO'}, "Collision generated")
        return {'FINISHED'}

class OBJECT_OT_generate_navmesh(Operator):
    bl_idname = "object.generate_navmesh"
    bl_label = "Generate Navmesh"
    bl_options = {'REGISTER', 'UNDO'}
    all_objects: BoolProperty(name="For all objects", default=False)

    def execute(self, context):
        # Logika navmesh...
        self.report({'INFO'}, "Navmesh generated")
        return {'FINISHED'}

class OBJECT_OT_delete_specific(Operator):
    bl_idname = "object.delete_specific"
    bl_label = "Delete Specific"
    target_type: StringProperty()
    scope: StringProperty()

    def execute(self, context):
        # Logika usuwania...
        return {'FINISHED'}

# --- UI PANEL ---
class VIEW3D_PT_collision_generator(Panel):
    bl_label = "AutoCollision & Nav"
    bl_idname = "VIEW3D_PT_collision_generator"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Collisions"

    def draw(self, context):
        layout = self.layout
        props = context.scene.collision_generator_props
        
        # Opcjonalnie logo też w panelu bocznym (małe)
        pcoll = preview_collections.get("main")
        if pcoll and "my_logo" in pcoll:
            layout.template_icon(icon_value=pcoll["my_logo"].icon_id, scale=2.0)

        layout.prop(props, "merge_selected", toggle=True, icon='GROUP')
        # Reszta UI...

# --- REJESTRACJA ---
CLASSES = [
    OQ_STUDIO_AddonPreferences,
    CollisionGeneratorProperties,
    OBJECT_OT_generate_collision,
    OBJECT_OT_generate_navmesh,
    OBJECT_OT_delete_specific,
    VIEW3D_PT_collision_generator,
]

def register():
    # 1. Ładowanie ikon
    global preview_collections
    pcoll = bpy.utils.previews.new()
    addon_path = os.path.dirname(__file__)
    icons_dir = os.path.join(addon_path, "assets")
    
    # Szukaj pliku assets/logo.png
    if os.path.exists(os.path.join(icons_dir, "logo.png")):
        pcoll.load("my_logo", os.path.join(icons_dir, "logo.png"), 'IMAGE')
    
    preview_collections["main"] = pcoll

    # 2. Rejestracja klas
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.collision_generator_props = PointerProperty(type=CollisionGeneratorProperties)

def unregister():
    # 1. Usuwanie ikon z pamięci
    global preview_collections
    for pcoll in preview_collections.values():
        bpy.utils.previews.remove(pcoll)
    preview_collections.clear()

    # 2. Wyrejestrowanie klas
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.collision_generator_props

if __name__ == "__main__":
    register()

