const contexts=$('Verify supplier quote').all();
return $input.all().flatMap((item,i)=>{
  const c=contexts[i]?.json, r=item.json;
  if(!c || !c.offers?.[0])return [];
  const o=c.offers[0], selected=c.selectedRoom;
  let allInclusive={status:'unknown',totalDKK:null,roomName:null};
  if(selected?.boardBasis==='AI')allInclusive={status:'included',totalDKK:o.total_price,roomName:selected.roomName};
  else if(!r.failure && r.basketId===c.basketId && Array.isArray(r.data) && r.data.length===1 && r.data[0].roomIndex===0) {
    const rooms=r.data[0].roomInfo?.availableHotelRooms;
    if(Array.isArray(rooms) && rooms.length) {
      const current=rooms.find(x=>x.roomId===selected?.roomId)?.availableBoards?.find(x=>x.boardSelect===selected?.boardSelect);
      if(current && typeof current.upgradePriceInLocaleCurrency==='number' && Number.isFinite(current.upgradePriceInLocaleCurrency)) {
        const options=[];let unknown=false;
        for(const room of rooms) {
          if(!Array.isArray(room.availableBoards)){unknown=true;continue;}
          for(const board of room.availableBoards) {
            if(board.boardBasis!=='AI')continue;
            if(typeof board.availability!=='number'){unknown=true;continue;}
            if(board.availability<=0)continue;
            const delta=board.upgradePriceInLocaleCurrency;
            if(typeof delta!=='number' || !Number.isFinite(delta)){unknown=true;continue;}
            const total=o.total_price+delta-current.upgradePriceInLocaleCurrency;
            if(Number.isFinite(total) && total>0)options.push({totalDKK:Math.ceil(total),roomName:room.roomName,roomChanged:room.roomId!==selected.roomId});
          }
        }
        options.sort((a,b)=>a.totalDKK-b.totalDKK);
        if(options.length)allInclusive={status:'available',...options[0]};
        else if(!unknown)allInclusive={status:'unavailable',totalDKK:null,roomName:null};
      }
    }
  }
  const {boardCookie,basketId,boardOptionsUrl,selectedRoom,...result}=c;
  return [{json:{...result,offers:[{...o,allInclusive}]}}];
});
