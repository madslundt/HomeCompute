const contexts=$('Build supplier quote').all();
return $input.all().flatMap((item,i)=>{
  const c=contexts[i]?.json, response=item.json, b=response.data?.basketData;
  if (!c || response.failure || response.data?.failure || !b || b.isExpired!==false
      || b.originalRequest?.countryId!=='DK' || !Number.isFinite(Date.parse(b.basketTtl)) || Date.parse(b.basketTtl)<=Date.parse($now.toISO())
      || typeof b.priceInLocalCurrencyTotal!=='number' || !Number.isSafeInteger(b.priceInLocalCurrencyTotal) || b.priceInLocalCurrencyTotal<=0
      || b.hotelId!==c.quoteBody.pageData.sapiId || b.hotelFromDate!==c.providerParams.departure || b.hotelToDate!==c.providerParams.return) return [];
  const rooms=b.selectedHotelRooms, flight=b.selectedFlight;
  if (!Array.isArray(rooms) || rooms.length!==1 || rooms[0].adults!==2 || !Array.isArray(rooms[0].childAges)
      || rooms[0].childAges.join(',')!==c.request.childAges.join(',') || !flight) return [];
  const out=flight.startLeg, back=flight.endLeg;
  if (out?.departureAirport!=='CPH' || back?.destinationAirport!=='CPH' || out.destinationAirport!==back.departureAirport
      || !Array.isArray(out.itinerary) || out.itinerary.length!==1 || !Array.isArray(back.itinerary) || back.itinerary.length!==1
      || String(out.itinerary[0].departureTime).slice(0,10)!==c.providerParams.departure
      || String(back.itinerary[0].departureTime).slice(0,10)!==c.providerParams.return) return [];
  if (out.itinerary[0].departureAirport!==out.departureAirport || out.itinerary[0].destinationAirport!==out.destinationAirport
      || back.itinerary[0].departureAirport!==back.departureAirport || back.itinerary[0].destinationAirport!==back.destinationAirport) return [];
  const fees=b.hotelFees;
  if (!fees || typeof fees.rawFeesDescription!=='string' || fees.rawFeesDescription.trim()
      || (fees.structuredFees!==null && JSON.stringify(fees.structuredFees)!=='[]')) return [];
  const o={...c.offer,total_price:b.priceInLocalCurrencyTotal,supplierVerified:true,providerUrl:c.providerUrl,
    supplierCheckedAt:$now.toISO(),advertisedTotal:c.offer.total_price,hotel:{...c.offer.hotel,
      room_name:[rooms[0].roomName,rooms[0].boardBasisName].filter(Boolean).join(', ')}};
  const signature=b.originalRequest?.request?.creatorSignature||'00000000-0000-0000-0000-000000000000';
  if(!/^[0-9a-f-]{36}$/i.test(b.basketId||'') || !/^[0-9a-f-]{36}$/i.test(signature))return [];
  return [{json:{offers:[o],n_search_results:1,dataLayer:c.dataLayer,request:c.request,
    basketId:b.basketId,selectedRoom:rooms[0],
    boardOptionsUrl:'https://www.solfaktor.dk/custom-api/yggdrasil/basket/getHotelRooms?basketId='+encodeURIComponent(b.basketId),
    boardCookie:'creatorSignature='+signature}}];
});
