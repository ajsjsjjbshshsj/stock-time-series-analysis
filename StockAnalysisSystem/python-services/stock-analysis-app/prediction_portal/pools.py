"""Read-only bridge from local classifications and immutable saved pool versions."""


class PredictionPools:
    def __init__(self,pools):self.pools=pools

    def choices(self):
        values={}
        offset=0
        while True:
            page=self.pools.versions(limit=50,offset=offset)
            for row in page['items']:
                values[row['pool_version']]=self.pools.get(row['pool_version'])
            offset+=len(page['items'])
            if offset>=page['total']:break
            if not page['items'] or offset>10000:raise RuntimeError('Pool catalog limit exceeded')
        # Same identity means unchanged source and members; use current as-of metadata.
        for row in self.pools.prediction_candidates():values[row['pool_version']]=row
        return list(values.values())

    def get(self,version):
        from app.repositories.web_pool_repository import PoolUnavailable
        try:return self.pools.get(version)
        except PoolUnavailable:pass
        # An unsaved choice is valid only while its complete content identity is current.
        # No write permission or mutable lookup token is granted to ordinary users.
        for row in self.pools.prediction_candidates():
            if row['pool_version']==version:return row
        raise LookupError('Pool changed or unavailable; refresh the catalog')
