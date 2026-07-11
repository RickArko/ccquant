{{
    config(
        materialized='table',
        schema='signals',
        tags=['macro']
    )
}}

select
  date,
  max(case when series_id = 'M2SL' then value end) as m2sl,
  max(case when series_id = 'WALCL' then value end) as walcl,
  max(case when series_id = 'DGS10' then value end) as dgs10,
  max(case when series_id = 'DGS2' then value end) as dgs2,
  max(case when series_id = 'T10YIE' then value end) as t10yie,
  max(case when series_id = 'FEDFUNDS' then value end) as fedfunds,
  max(case when series_id = 'DTWEXBGS' then value end) as dtwexbgs,
  max(case when series_id = 'VIXCLS' then value end) as vixcls
from {{ ref('stg_macro_series') }}
group by date
