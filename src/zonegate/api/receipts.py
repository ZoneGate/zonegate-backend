from litestar import Controller, get
from litestar.di import NamedDependency
from litestar.exceptions import NotFoundException
from litestar.params import FromPath
from zonegate.authorization.service import AuthorizationService
from zonegate.domain.receipts import Receipt


class ReceiptsController(Controller):
    path = "/v1/receipts"

    @get("/{receipt_id:str}")
    async def get_receipt(
        self,
        receipt_id: FromPath[str],
        auth_service: NamedDependency[AuthorizationService],
    ) -> Receipt:
        """Retrieves an issued action receipt record by receipt ID."""
        receipt = await auth_service.store.get_receipt(receipt_id)
        if not receipt:
            raise NotFoundException(detail=f"Receipt '{receipt_id}' not found")
        return receipt
