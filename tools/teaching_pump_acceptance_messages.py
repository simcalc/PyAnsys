"""Explain native completion versus numerical rejection; never grant admission."""
import math

def finite(value):return type(value) in (int,float) and math.isfinite(value)
def mapping(value):return value if isinstance(value,dict) else {}
def criterion(name,value,limit,relation,unit):
    available=finite(value) and finite(limit)
    passed=available and (value>limit if relation=='>' else value<=limit)
    return {'name':name,'actual':value if available else None,'limit':limit,'relation':relation,'unit':unit,
        'evidenceAvailable':available,'criterionSatisfied':bool(passed)}

def describe(result):
    """Secondary log projection only. Preserve every authoritative flag/status."""
    wanted=('status','actualCfdRunVerified','numericalBaselineQualified','qualifiedBaseline','mainApplicationWrites')
    output={k:result[k] for k in wanted}
    observation=mapping(result.get('nativeObservation'));quality=mapping(observation.get('quality'))
    acceptance=mapping(mapping(result.get('study')).get('acceptance'))
    stages=mapping(result.get('stages'));head=mapping(mapping(stages.get('head')).get('result')).get('value')
    residuals=mapping(quality.get('finalResidualHistory'));head_history=quality.get('headHistoryM')
    span=None
    if isinstance(head_history,list) and head_history and all(finite(v) for v in head_history):
        mean=sum(head_history)/len(head_history)
        if abs(mean)>0:span=(max(head_history)-min(head_history))/abs(mean)
    output['criteria']=[criterion('Positive extended assembly head',head,0.,'>','m'),
        criterion('Reverse-flow fraction',quality.get('reverseFlowFraction'),acceptance.get('reverseFlowFractionLimit'),'<=' ,'1'),
        criterion('Final-window relative head span',span,acceptance.get('headWindowRelativeLimit'),'<=' ,'1')]
    for field in ('p','U','k','omega'):
        data=residuals.get(field)
        value=max(data) if isinstance(data,list) and data and all(finite(v) for v in data) else None
        output['criteria'].append(criterion(field+' maximum initial nonlinear residual',value,acceptance.get('residualLimit'),'<=' ,'1'))
    output['reasonCodes']=[row['reasonCode'] for row in result.get('diagnostics',[]) if isinstance(row,dict) and isinstance(row.get('reasonCode'),str)]
    output['explanation']=('Native CFD execution completed; numerical acceptance is not satisfied. Exit2 preserves the rejection.'
        if result['actualCfdRunVerified'] and not result['numericalBaselineQualified'] else
        'Native completion and numerical qualification are reported separately; this log projection grants no additional acceptance.')
    output['headMeasurement']=mapping(observation.get('physicalBasis')).get('headMeasurement')
    output['authority']='Python reducer result; secondary log explanation only'
    return output
