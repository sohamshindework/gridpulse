"""Pure policy decisions shared by the simulator and Kubernetes planner."""

def policy(level, *, workload='training', preemptible=True, mode='hybrid',
           current_region=None, allowed_regions=None, regional_levels=None):
    if type(level) is not int or level not in (0, 1, 2, 3):
        raise ValueError('Flex level must be 0, 1, 2 or 3')
    if mode not in ('hybrid', 'power-cap', 'checkpoint'):
        raise ValueError('Unknown policy mode')
    if workload not in ('training', 'batch', 'inference'):
        raise ValueError('Unknown workload class')
    if workload == 'inference':
        return {'level': level, 'action': 'protected', 'powerFraction': 1., 'throughputFraction': 1.,
                'parallelismFraction': 1., 'checkpointRequired': False, 'migrationCandidate': None}
    # Throughput values are simulator assumptions, NOT empirical GPU results.
    power, throughput, parallel = [(1., 1., 1.), (.85, .95, 1.), (.65, .80, .75), (.60, .72, .50)][level]
    pause = level == 3 and preemptible and mode in ('hybrid', 'checkpoint')
    if mode == 'power-cap': parallel = 1.
    if mode == 'checkpoint' and level < 3: power, throughput, parallel = 1., 1., 1.
    candidate = None
    if level >= 2 and current_region and allowed_regions and regional_levels:
        choices = [(value, region) for region, value in regional_levels.items()
                   if region in allowed_regions and region != current_region and value is not None and value < level]
        if choices: candidate = min(choices)[1]
    return {'level': level, 'action': 'checkpoint-and-pause' if pause else 'normal' if level == 0 else 'power-cap-and-scale',
            'powerFraction': 0. if pause else power, 'throughputFraction': 0. if pause else throughput,
            'parallelismFraction': 0. if pause else parallel, 'checkpointRequired': pause or parallel < 1.,
            'migrationCandidate': candidate, 'migrationAutomatic': False,
            'batchScaleHint': parallel, 'throughputBasis': 'illustrative profile; calibrate before hardware claims'}
