-- Every symbol in the canonical panel must be in the active universe.
select m.symbol
from {{ ref('mart_signals_daily') }} m
left join {{ ref('dim_assets') }} d on m.symbol = d.symbol
where d.symbol is null
