bl_info = {
    "name": "AutoCollision & Navmesh Ultimate (Hybrid Perfect)",
    "blender": (4, 1, 0),
    "category": "Object",
    "author": "OshQRD Studio",
    "version": (4, 0),
    "description": "Combines v3.30 Navmesh stability with v3.90 Collision improvements. No .001 duplicates, no coordinate shifts.",
    "location": "View3D > Collisions",
    "warning": "",
    "support": "COMMUNITY",
    "license": "Personal, Non-Commercial, No-Derivatives",
}

import bpy
import bmesh
import math
from bpy.props import BoolProperty, EnumProperty, PointerProperty, FloatProperty, StringProperty
from bpy.types import Operator, PropertyGroup, Panel
from mathutils import Vector

# --- HELPER: BEZPIECZNE SPRAWDZANIE ---
def is_valid(obj):
    """Sprawdza czy obiekt istnieje (zapobiega ReferenceError)."""
    try:
        return obj is not None and obj.name in bpy.data.objects
    except (ReferenceError, AttributeError):
        return False

# --- HELPER: MATERIAŁY ---
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
    try: mat.shadow_method = 'NONE'
    except AttributeError: pass
    mat.show_transparent_back = False 

    if obj.data.materials: obj.data.materials[0] = mat
    else: obj.data.materials.append(mat)

# --- HELPER: BBOX DISTANCE ---
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
    if threshold <= 0.0 or threshold > 1000.0: return [objects]

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

# --- HELPER: MERGE LOGIC ---
def duplicate_and_merge(context, objects, method='JOIN'):
    bpy.ops.object.select_all(action='DESELECT')
    
    if method == 'JOIN':
        duplicates = []
        for obj in objects:
            if not is_valid(obj): continue
            new_obj = obj.copy()
            new_obj.data = obj.data.copy()
            context.collection.objects.link(new_obj)
            new_obj.parent = None
            new_obj.matrix_world = obj.matrix_world.copy()
            new_obj.select_set(True)
            duplicates.append(new_obj)
        
        if not duplicates: return None
        context.view_layer.objects.active = duplicates[0]
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        bpy.ops.object.join()
        return context.active_object

    elif method == 'BOOLEAN':
        if not is_valid(objects[0]): return None
        base = objects[0].copy()
        base.data = objects[0].data.copy()
        context.collection.objects.link(base)
        base.parent = None
        base.matrix_world = objects[0].matrix_world.copy()
        
        context.view_layer.objects.active = base
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        
        for obj in objects[1:]:
            if not is_valid(obj): continue
            temp = obj.copy()
            temp.data = obj.data.copy()
            context.collection.objects.link(temp)
            temp.parent = None
            temp.matrix_world = obj.matrix_world.copy()
            
            context.view_layer.objects.active = temp
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
            
            mod = base.modifiers.new(name="UnionMerge", type='BOOLEAN')
            mod.object = temp
            mod.operation = 'UNION'
            mod.solver = 'FAST'
            
            context.view_layer.objects.active = base
            try: bpy.ops.object.modifier_apply(modifier=mod.name)
            except Exception: pass
            
            bpy.data.objects.remove(temp, do_unlink=True)
        return base

# --- CLEANING HELPERS (Z V3.90 - Najlepsze) ---
def is_collision_name(name):
    suffixes = ['-col', '-colonly', '-navmesh']
    for s in suffixes:
        if name.endswith(s): return True
        if s + "." in name: return True 
    return False

def remove_existing_collision(obj):
    """Usuwa WSZYSTKIE kolizje (-col i -colonly)."""
    if not is_valid(obj): return
    objects_to_delete = set()
    for child in obj.children:
        if is_valid(child) and is_collision_name(child.name):
            objects_to_delete.add(child)
    
    base_name = obj.name
    for o in list(bpy.data.objects):
        if is_valid(o) and o.name.startswith(base_name) and is_collision_name(o.name):
             if "-col" in o.name or "-colonly" in o.name:
                objects_to_delete.add(o)

    for victim in objects_to_delete:
        if is_valid(victim) and victim != obj:
            try: bpy.data.objects.remove(victim, do_unlink=True)
            except: pass

def remove_existing_navmesh(obj):
    """Usuwa WSZYSTKIE navmeshy."""
    if not is_valid(obj): return
    objects_to_delete = set()
    for child in obj.children:
        if is_valid(child) and "-navmesh" in child.name:
            objects_to_delete.add(child)
    base_name = obj.name
    for o in list(bpy.data.objects):
        if is_valid(o) and o.name.startswith(base_name) and "-navmesh" in o.name:
            objects_to_delete.add(o)

    for victim in objects_to_delete:
        if is_valid(victim) and victim != obj:
            try: bpy.data.objects.remove(victim, do_unlink=True)
            except: pass

def is_generated(obj):
    if not is_valid(obj): return False
    return is_collision_name(obj.name)

def is_generated_navmesh(obj):
    return obj and "-navmesh" in obj.name

# --- PROPERTIES ---
class CollisionGeneratorProperties(PropertyGroup):
    merge_selected: BoolProperty(name="Merge Selected", default=False)
    merge_distance: FloatProperty(name="Merge Dist", default=0.05, min=0.0, unit='LENGTH')
    
    collision_suffix: EnumProperty(items=[('-col', "-col", ""), ('-colonly', "-colonly", "")], default='-col')
    collision_detail: EnumProperty(
        name="Detail",
        items=[('BOUNDS', "Bounding Box", ""), ('CONVEX', "Convex Hull", ""), ('EXACT', "Exact Copy", "")],
        default='BOUNDS'
    )

    nav_max_angle: FloatProperty(name="Max Slope", default=45.0, max=89.9, subtype='ANGLE')
    nav_offset: FloatProperty(name="Lift (m)", default=0.05, unit='LENGTH')
    nav_decimation: FloatProperty(name="Simplify", default=0.8, min=0.01, max=1.0, subtype='FACTOR')

# --- OPERATOR: KOLIZJE (Z V3.90 - Ten dobry, co nie robi .001) ---
class OBJECT_OT_generate_collision(Operator):
    bl_idname = "object.generate_collision"
    bl_label = "Generate Collision"
    bl_options = {'REGISTER', 'UNDO'}
    all_objects: BoolProperty(name="For all objects", default=False)

    def execute(self, context):
        props = context.scene.collision_generator_props
        suffix = props.collision_suffix
        detail = props.collision_detail
        merge = props.merge_selected
        dist_limit = props.merge_distance
        
        if context.view_layer: context.view_layer.update()
        
        objects = [obj for obj in (context.selected_objects if not self.all_objects else context.scene.objects) 
                   if is_valid(obj) and obj.type == 'MESH' and not is_generated(obj)]
        
        if not objects: return {'CANCELLED'}
        created = 0

        if merge:
            groups = group_objects_by_bbox_distance(objects, dist_limit)
            for group in groups:
                if not group: continue
                group = [m for m in group if is_valid(m)]
                if not group: continue

                parent_obj = group[0]
                if context.view_layer.objects.active in group: parent_obj = context.view_layer.objects.active

                for member in group: remove_existing_collision(member)

                if len(group) > 1:
                    merged_obj = duplicate_and_merge(context, group, method='JOIN')
                    if merged_obj:
                        self.process_geometry(context, merged_obj, parent_obj, suffix, detail)
                else:
                    self.create_single_collision(context, group[0], suffix, detail)
                created += 1
            self.report({'INFO'}, f"Created {created} merged collision groups")
        else:
            for obj in objects:
                if not is_valid(obj): continue
                remove_existing_collision(obj)
                self.create_single_collision(context, obj, suffix, detail)
                created += 1
            self.report({'INFO'}, f"Generated {created} collisions")

        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            if is_valid(obj): obj.select_set(True)
        return {'FINISHED'}

    def create_single_collision(self, context, obj, suffix, detail):
        if not is_valid(obj): return
        bpy.ops.object.select_all(action='DESELECT')
        new_obj = obj.copy()
        new_obj.data = obj.data.copy()
        context.collection.objects.link(new_obj)
        
        wm = new_obj.matrix_world.copy()
        new_obj.parent = None
        new_obj.matrix_world = wm
        self.process_geometry(context, new_obj, obj, suffix, detail)

    def process_geometry(self, context, working_obj, parent_obj, suffix, detail):
        if not is_valid(parent_obj): return
        final_name = parent_obj.name + suffix
        
        context.view_layer.objects.active = working_obj
        working_obj.select_set(True)

        if detail == 'BOUNDS':
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
            min_c, max_c = get_world_bbox(working_obj)
            center = (min_c + max_c) / 2
            size = max_c - min_c
            
            # Najpierw usuń tymczasowy, potem stwórz nowy (FIX .001)
            bpy.data.objects.remove(working_obj, do_unlink=True)
            
            bpy.ops.object.select_all(action='DESELECT') 
            bpy.ops.mesh.primitive_cube_add(location=center) 
            working_obj = context.active_object
            
            working_obj.name = final_name
            working_obj.scale = size / 2
            bpy.ops.object.transform_apply(scale=True)
            
        elif detail == 'CONVEX':
            working_obj.name = final_name
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.convex_hull(delete_unused=True)
            bpy.ops.object.mode_set(mode='OBJECT')
        
        elif detail == 'EXACT':
            working_obj.name = final_name

        world_mtx = working_obj.matrix_world.copy()
        working_obj.parent = parent_obj
        working_obj.matrix_world = world_mtx
        
        working_obj.display_type = 'SOLID'
        working_obj.show_wire = True
        working_obj.hide_render = True
        assign_transparent_material(working_obj, "AutoCollision_MAT", (0.0, 0.3, 1.0, 0.3))


# --- OPERATOR: NAVMESH (Z V3.30 - Ten stabilny z "Total Detach") ---
class OBJECT_OT_generate_navmesh(Operator):
    bl_idname = "object.generate_navmesh"
    bl_label = "Generate Navmesh"
    bl_options = {'REGISTER', 'UNDO'}
    all_objects: BoolProperty(name="For all objects", default=False)

    def execute(self, context):
        props = context.scene.collision_generator_props
        suffix = '-navmesh'
        merge = props.merge_selected
        dist_limit = props.merge_distance
        
        if context.view_layer: context.view_layer.update()

        # 1. Zidentyfikuj zaznaczone
        selected_objects = [obj for obj in (context.selected_objects if not self.all_objects else context.scene.objects) 
                   if is_valid(obj) and obj.type == 'MESH' and not any(obj.name.endswith(s) for s in ['-col', '-colonly', '-navmesh'])]

        if not selected_objects: return {'CANCELLED'}
        
        # 2. Zidentyfikuj starych rodziców
        affected_parents_map = {}
        for obj in selected_objects:
            if obj.parent and is_generated_navmesh(obj.parent):
                p = obj.parent
                if p not in affected_parents_map:
                    # Zbierz WSZYSTKIE dzieci tego rodzica (nawet te niezaznaczone)
                    all_siblings = [child for child in p.children if child.type == 'MESH' and not is_generated_navmesh(child)]
                    affected_parents_map[p] = all_siblings

        # 3. KROK NUKLEARNY: ODKOTWICZ WSZYSTKICH (Selected + Siblings)
        objects_to_unparent = set(selected_objects)
        for siblings in affected_parents_map.values():
            objects_to_unparent.update(siblings)
            
        for obj in objects_to_unparent:
            if is_valid(obj):
                wm = obj.matrix_world.copy()
                obj.parent = None
                obj.matrix_world = wm
                # Używamy nowszego helpera do czyszczenia
                remove_existing_navmesh(obj)

        context.view_layer.update()

        # 4. USUŃ STARYCH RODZICÓW
        for old_navmesh in affected_parents_map.keys():
            try: bpy.data.objects.remove(old_navmesh, do_unlink=True)
            except: pass

        # 5. GENERUJ NAVMESH DLA ZAZNACZONYCH (NOWA GRUPA)
        created_count = 0
        groups = group_objects_by_bbox_distance(selected_objects, dist_limit) if merge else [[obj] for obj in selected_objects]

        for group in groups:
            group = [m for m in group if is_valid(m)]
            if not group: continue
            leader = group[0]
            if context.view_layer.objects.active in group: leader = context.view_layer.objects.active
            
            self.create_navmesh_for_group(context, group, leader.name + suffix, props)
            created_count += 1

        # 6. ODBUDUJ NAVMESHY DLA POZOSTAŁYCH (STARE GRUPY)
        for old_navmesh_obj, original_siblings in affected_parents_map.items():
            leftovers = [s for s in original_siblings if s not in selected_objects and is_valid(s)]
            
            if leftovers:
                leader_leftover = leftovers[0]
                self.create_navmesh_for_group(context, leftovers, leader_leftover.name + suffix, props)
                created_count += 1

        bpy.ops.object.select_all(action='DESELECT')
        for obj in selected_objects: 
            if is_valid(obj): obj.select_set(True)
        self.report({'INFO'}, f"Generated & Reorganized Navmeshes")
        return {'FINISHED'}

    def create_navmesh_for_group(self, context, group, name, props):
        if not group: return
        if len(group) > 1:
            merged_obj = duplicate_and_merge(context, group, method='BOOLEAN')
        else:
            obj = group[0]
            bpy.ops.object.select_all(action='DESELECT')
            merged_obj = obj.copy()
            merged_obj.data = obj.data.copy()
            context.collection.objects.link(merged_obj)
            merged_obj.parent = None
            merged_obj.matrix_world = obj.matrix_world.copy()
            context.view_layer.objects.active = merged_obj
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        if not merged_obj: return

        OBJECT_OT_generate_navmesh.process_navmesh_geometry_static(context, merged_obj, props)

        merged_obj.name = name
        bpy.ops.object.select_all(action='DESELECT')
        merged_obj.select_set(True)
        context.view_layer.objects.active = merged_obj
        bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')

        context.view_layer.update()
        for member in group:
            if is_valid(member):
                member_mtx = member.matrix_world.copy()
                member.parent = merged_obj
                member.matrix_world = member_mtx

    @staticmethod
    def process_navmesh_geometry_static(context, working_obj, props):
        context.view_layer.objects.active = working_obj
        working_obj.select_set(True)
        
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(working_obj.data)
        bm.faces.ensure_lookup_table()
        
        up = Vector((0, 0, 1))
        max_angle = props.nav_max_angle 
        to_delete = [f for f in bm.faces if f.normal.angle(up) > max_angle]
        bmesh.ops.delete(bm, geom=to_delete, context='FACES_ONLY')
        bmesh.update_edit_mesh(working_obj.data)
        bpy.ops.object.mode_set(mode='OBJECT')
        
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.delete_loose()
        bpy.ops.object.mode_set(mode='OBJECT')

        if props.nav_decimation < 1.0:
            mod = working_obj.modifiers.new("Decimate", 'DECIMATE')
            mod.ratio = props.nav_decimation
            bpy.ops.object.modifier_apply(modifier=mod.name)

        if props.nav_offset != 0.0:
             bpy.ops.object.mode_set(mode='EDIT')
             bpy.ops.mesh.select_all(action='SELECT')
             bpy.ops.transform.translate(value=(0, 0, props.nav_offset))
             bpy.ops.object.mode_set(mode='OBJECT')

        working_obj.display_type = 'SOLID'
        working_obj.show_wire = True
        working_obj.hide_render = True
        assign_transparent_material(working_obj, "AutoNavmesh_MAT", (1.0, 0.0, 0.0, 0.4))

# --- OPERATOR: USUWANIE (Z V3.30 - Ten prosty) ---
class OBJECT_OT_delete_specific(Operator):
    bl_idname = "object.delete_specific"
    bl_label = "Delete Specific"
    bl_options = {'REGISTER', 'UNDO'}
    target_type: StringProperty()
    scope: StringProperty()

    def execute(self, context):
        suffixes = []
        if self.target_type == 'COL': suffixes = ['-col', '-colonly']
        elif self.target_type == 'NAV': suffixes = ['-navmesh']
        
        if context.view_layer: context.view_layer.update()
        
        check = list(context.scene.objects) if self.scope == 'ALL' else list(context.selected_objects)
        deleted = 0
        
        for obj in check:
            # Dodane zabezpieczenie is_valid, ale logika iteracji z v3.30 (prosta)
            if not is_valid(obj): continue

            try:
                if any(obj.name.endswith(s) for s in suffixes):
                    children = [child for child in obj.children if is_valid(child)]
                    for child in children:
                        mat_world = child.matrix_world.copy()
                        child.parent = None
                        child.matrix_world = mat_world
                    
                    bpy.data.objects.remove(obj, do_unlink=True)
                    deleted += 1
                    continue
                
                children_to_remove = [c for c in obj.children if is_valid(c) and any(c.name == obj.name + s for s in suffixes)]
                for c in children_to_remove:
                    bpy.data.objects.remove(c, do_unlink=True)
                    deleted += 1
            except ReferenceError: continue
        self.report({'INFO'}, f"Deleted {deleted}")
        return {'FINISHED'}

# --- UI PANEL ---
class VIEW3D_PT_collision_generator(Panel):
    bl_label = "AutoCollision & Nav"
    bl_idname = "VIEW3D_PT_collision_generator"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Collisions"

    def draw(self, context):
        props = context.scene.collision_generator_props
        layout = self.layout

        box_merge = layout.box()
        row = box_merge.row()
        row.prop(props, "merge_selected", toggle=True, icon='GROUP')
        if props.merge_selected:
            row = box_merge.row()
            row.prop(props, "merge_distance")

        box_col = layout.box()
        box_col.label(text="COLLISIONS (Child)", icon='MESH_CUBE')
        box_col.prop(props, "collision_suffix", text="")
        box_col.prop(props, "collision_detail", text="Detail")
        row = box_col.row(align=True)
        row.scale_y = 1.2
        row.operator("object.generate_collision", text="Create Selected").all_objects = False
        row.operator("object.generate_collision", text="Create All").all_objects = True
        row_del = box_col.row(align=True)
        op = row_del.operator("object.delete_specific", text="Del Selected", icon='X')
        op.target_type, op.scope = 'COL', 'SELECTED'
        op = row_del.operator("object.delete_specific", text="Del All", icon='TRASH')
        op.target_type, op.scope = 'COL', 'ALL'

        layout.separator()

        box_nav = layout.box()
        box_nav.label(text="NAVMESH (Parent)", icon='OUTLINER_DATA_LATTICE')
        col = box_nav.column(align=True)
        col.prop(props, "nav_max_angle")
        col.prop(props, "nav_offset")
        col.prop(props, "nav_decimation")
        row = box_nav.row(align=True)
        row.operator("object.generate_navmesh", text="Create Selected").all_objects = False
        row.operator("object.generate_navmesh", text="Create All").all_objects = True
        row_del = box_nav.row(align=True)
        op = row_del.operator("object.delete_specific", text="Del Selected", icon='X')
        op.target_type, op.scope = 'NAV', 'SELECTED'
        op = row_del.operator("object.delete_specific", text="Del All", icon='TRASH')
        op.target_type, op.scope = 'NAV', 'ALL'

def register():
    bpy.utils.register_class(CollisionGeneratorProperties)
    bpy.utils.register_class(OBJECT_OT_generate_collision)
    bpy.utils.register_class(OBJECT_OT_generate_navmesh)
    bpy.utils.register_class(OBJECT_OT_delete_specific)
    bpy.utils.register_class(VIEW3D_PT_collision_generator)
    bpy.types.Scene.collision_generator_props = PointerProperty(type=CollisionGeneratorProperties)

def unregister():
    del bpy.types.Scene.collision_generator_props
    bpy.utils.unregister_class(VIEW3D_PT_collision_generator)
    bpy.utils.unregister_class(OBJECT_OT_delete_specific)
    bpy.utils.unregister_class(OBJECT_OT_generate_navmesh)
    bpy.utils.unregister_class(OBJECT_OT_generate_collision)
    bpy.utils.unregister_class(CollisionGeneratorProperties)

if __name__ == "__main__":
    register()