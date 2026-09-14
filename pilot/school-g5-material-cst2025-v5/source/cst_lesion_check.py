"""Read-only CST checks for the four-site tooth and unfed passive loop."""
from pathlib import Path
from math import sin, cos, radians, pi, isfinite
import hashlib
import json

def validate(project, archive: Path, manifest: dict):
    p=manifest['parameters'] if 'parameters' in manifest else manifest['effective_parameters']
    placement=manifest['derived_geometry']['lesion_placement']
    center=placement['lesion_center_post_tilt_mm']
    selected_solid=placement.get('selected_solid','lesion')
    all_centers=placement.get('all_site_centers_mm',{placement.get('site','selected'):center})
    r=p['lesion_radius_mm'];depth=p['lesion_depth_mm']
    tx=radians(p['tilt_x_deg']);ty=radians(p['tilt_y_deg'])
    distance=r-depth/2
    point=[center[0]+distance*sin(ty)*cos(tx),center[1]-distance*sin(tx),center[2]+distance*cos(ty)*cos(tx)]
    point=list(center)
    raw=archive/'cst-lesion-check.txt'
    macro='Sub Main()\nOpen "'+str(raw).replace('"','""')+'" For Output As #1\n'
    macro+=f'Print #1, "volume=" & CStr(Solid.GetVolume("phantom:{selected_solid}"))\n'
    for site in all_centers:
        macro+=f'Print #1, "volume_{site}=" & CStr(Solid.GetVolume("phantom:inclusion_{site}"))\n'
    fixture=p.get('include_fixture',False)
    counterface=p.get('include_counterface',False)
    counterface_solids=[]
    if counterface:
        carrier_sides=['left','right','front','back']
        copper_sides=list(carrier_sides)
        mode=p.get('passive_loop_mode','closed')
        if mode in ('single_split','double_split'):
            copper_sides.remove('back')
            copper_sides.extend(('back_left','back_right'))
        if mode == 'double_split':
            copper_sides.remove('front')
            copper_sides.extend(('front_left','front_right'))
        counterface_solids=(
            [f'counterface:fr4_{side}' for side in carrier_sides]
            + [f'counterface:copper_{side}' for side in copper_sides]
        )
    fixture_solids=(['fixture:socket','fixture:left_front_post','fixture:left_back_post',
        'fixture:right_front_post','fixture:right_back_post','fixture:top_left',
        'fixture:top_right','fixture:top_front','fixture:top_back','fixture:root_collar']
        if fixture else [])
    solids=['phantom:shell','phantom:core','phantom:pulp']+fixture_solids+counterface_solids
    for solid in solids:
        macro+=f'Print #1, "solid_{solid}=" & CStr(Solid.GetVolume("{solid}"))\n'
    cx=p['tooth_offset_x_mm']
    cy=11.8+p['tooth_offset_y_mm']
    z0=p['substrate_h_mm']+p['copper_t_mm']+p['airgap_mm']
    s=p['tooth_scale']
    post=p.get('fixture_post_width_mm',1.5)
    bridge=p.get('fixture_bridge_thickness_mm',1.2)
    reference_airgap=p.get('fixture_reference_airgap_mm',0.2)
    carrier_top=(p['substrate_h_mm']+p['copper_t_mm']+reference_airgap+8.4*s+
                 p['counterface_gap_mm']+p['copper_t_mm']+p['substrate_h_mm'])
    fixture_cy=11.8
    for label, (solid,xyz) in {
        'socket_wall':('socket',(6.0,fixture_cy,z0+2.5*s)),
        'open_bottom':('socket',(0.0,fixture_cy,z0+1*s)),
        'crown_cavity':('socket',(cx,cy,z0+3*s)),
        'post':('left_front_post',(-11+post/2,fixture_cy-9+post/2,2.0)),
        'top_clamp':('top_left',(-10.0,fixture_cy,carrier_top+bridge/2)),
        'collar_wall':('root_collar',(3.6,fixture_cy,carrier_top+bridge+0.75)),
        'collar_aperture':('root_collar',(0.0,fixture_cy,carrier_top+bridge+0.75)),
    }.items():
        if not fixture: continue
        macro+=f'Print #1, "holder_{label}=" & CStr(Solid.IsPointInsideShape({xyz[0]}, {xyz[1]}, {xyz[2]}, "fixture:{solid}"))\n'
    if counterface:
        counterface_geometry=manifest['derived_geometry']['passive_loop']
        aperture_point=(0.0,11.8,counterface_geometry['copper_lower_z_mm']+p['copper_t_mm']/2)
        for solid in counterface_solids:
            key=solid.replace(':','_')
            macro+=f'Print #1, "aperture_{key}=" & CStr(Solid.IsPointInsideShape({aperture_point[0]}, {aperture_point[1]}, {aperture_point[2]}, "{solid}"))\n'
    for shape in (selected_solid,'shell','core','pulp'):
        key='lesion' if shape==selected_solid else shape
        macro+=f'Print #1, "inside_{key}=" & CStr(Solid.IsPointInsideShape({point[0]:.12g}, {point[1]:.12g}, {point[2]:.12g}, "phantom:{shape}"))\n'
    for site,site_point in all_centers.items():
        macro+=f'Print #1, "inside_own_{site}=" & CStr(Solid.IsPointInsideShape({site_point[0]:.12g}, {site_point[1]:.12g}, {site_point[2]:.12g}, "phantom:inclusion_{site}"))\n'
        for host in ('shell','core','pulp'):
            macro+=f'Print #1, "inside_{host}_{site}=" & CStr(Solid.IsPointInsideShape({site_point[0]:.12g}, {site_point[1]:.12g}, {site_point[2]:.12g}, "phantom:{host}"))\n'
    macro+='Close #1\nEnd Sub'
    (archive/'cst-lesion-check.vba').write_text(macro,encoding='utf-8')
    project.model3d._execute_vba_code(macro)
    values=dict(line.strip().split('=',1) for line in raw.read_text().splitlines() if '=' in line)
    volume=float(values['volume'].replace(',','.'))
    target=placement['target_volume_mm3']
    if abs(volume-target)/target>0.001:
        raise ValueError(f'Fixed inclusion volume changed: {volume} versus {target}')
    solid_volumes={key:float(value.replace(',','.')) for key,value in values.items() if key.startswith('solid_')}
    if any(not isfinite(value) or value<=0 for value in solid_volumes.values()):
        raise ValueError(f'Invalid tooth or holder solids: {solid_volumes}')
    inclusion_volumes={site:float(values['volume_'+site].replace(',','.')) for site in all_centers}
    if any(abs(value-target)/target>0.001 for value in inclusion_volumes.values()):
        raise ValueError(f'Common-topology inclusion volumes changed: {inclusion_volumes}')
    site_inside={site:{'own':values['inside_own_'+site].strip().lower() in ('true','-1'),
        **{host:values[f'inside_{host}_{site}'].strip().lower() in ('true','-1') for host in ('shell','core','pulp')}}
        for site in all_centers}
    if any(not check['own'] or any(check[host] for host in ('shell','core','pulp')) for check in site_inside.values()):
        raise ValueError(f'Common-topology site containment failed: {site_inside}')
    holder={key:values['holder_'+key].strip().lower() in ('true','-1') for key in (
        'socket_wall','open_bottom','crown_cavity','post','top_clamp','collar_wall','collar_aperture')} if fixture else {}
    expected_holder=dict(socket_wall=True,open_bottom=False,crown_cavity=False,
                         post=True,top_clamp=True,collar_wall=True,collar_aperture=False)
    if fixture and holder != expected_holder:
        raise ValueError(f'Holder cavity/support check failed: {holder}')
    aperture={solid:values['aperture_'+solid.replace(':','_')].strip().lower() in ('true','-1') for solid in counterface_solids}
    if counterface and any(aperture.values()):
        raise ValueError(f'Passive-loop root aperture is blocked at its centre: {aperture}')
    inside={s:values['inside_'+s].strip().lower() in ('true','-1') for s in ('lesion','shell','core','pulp')}
    if not isfinite(volume) or volume<=1e-8 or volume>4*pi*r**3/3*(1+1e-5):
        raise ValueError(f'Invalid clipped lesion volume: {volume}')
    if not inside['lesion'] or any(inside[s] for s in ('shell','core','pulp')):
        raise ValueError(f'Lesion sample is missing or overlaps a host layer: {inside}')
    result={'passed':True,'cst_lesion_volume_mm3':volume,'sample_point_mm':point,'sample_inside_shapes':inside,'scope':'Actual positive final clipped-solid volume and one interior point; does not prove all-volume non-overlap or mesh convergence.',
            'diagnostic_sha256':{name:hashlib.sha256((archive/name).read_bytes()).hexdigest() for name in ('cst-lesion-check.txt','cst-lesion-check.vba')}}
    result.update(solid_volumes_mm3=solid_volumes,holder_sample_checks=holder,
        counterface_included=counterface,counterface_aperture_centre_clear=counterface and not any(aperture.values()),
        passive_loop_mode=p.get('passive_loop_mode') if counterface else None,
        counterface_aperture_sample_checks=aperture,
        all_inclusion_volumes_mm3=inclusion_volumes,all_site_inside_checks=site_inside,
        common_topology_checked=True,selected_solid=selected_solid)
    (archive/'cst-lesion-check.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
