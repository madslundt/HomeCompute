const r=$input.first().json;
return [{json:{ok:true,status:Number.isInteger(r.statusCode)?r.statusCode:0}}];
